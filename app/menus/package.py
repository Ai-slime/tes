# NOTE: url points to main branch placeholder; replace in repo as needed
import json
import sys
from datetime import datetime

import requests
from app.service.auth import AuthInstance
from app.client.engsel import (
    get_family,
    get_package,
    get_addons,
    get_package_details,
    send_api_request,
    unsubscribe,
)
from app.client.ciam import get_auth_code
from app.service.bookmark import BookmarkInstance
from app.client.purchase.redeem import settlement_bounty, settlement_loyalty, bounty_allotment
from app.menus.util import clear_screen, pause, display_html
from app.client.purchase.qris import show_qris_payment
from app.client.purchase.ewallet import show_multipayment
from app.client.purchase.balance import settlement_balance
from app.type_dict import PaymentItem
from app.menus.purchase import purchase_n_times, purchase_n_times_by_option_code
from app.menus.util import format_quota_byte
from app.service.decoy import DecoyInstance
from app.console import console, print_cyber_panel, cyber_input, loading_animation, print_step
from rich.table import Table
from rich.panel import Panel
from rich.align import Align

# Indonesian month short names mapping
_MONTH_ID = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "Mei", 6: "Jun",
    7: "Jul", 8: "Agu", 9: "Sep", 10: "Okt", 11: "Nov", 12: "Des"
}

def _format_ts(ts):
    try:
        # some APIs return milliseconds; detect and convert
        if isinstance(ts, (int, float)):
            if ts > 1e12:  # ms
                ts = int(ts / 1000)
            dt = datetime.fromtimestamp(int(ts))
            mon = _MONTH_ID.get(dt.month, dt.strftime("%b"))
            return f"{dt.day:02d} {mon} {dt.year} {dt.strftime('%H:%M:%S')}"
        return str(ts)
    except Exception:
        return str(ts)

def _days_until(ts):
    try:
        if not isinstance(ts, (int, float)):
            return None
        if ts > 1e12:
            ts = int(ts / 1000)
        now = datetime.now()
        target = datetime.fromtimestamp(int(ts))
        delta = target - now
        return delta.days
    except Exception:
        return None

def _get_bar_width(min_w: int = 12, max_w: int = 48, reserved: int = 60) -> int:
    try:
        total = console.size.width or 80
        avail = max(10, total - reserved)
        return max(min_w, min(max_w, avail))
    except Exception:
        return min_w

def _render_progress_bar(used: int, total: int, width: int | None = None, fill_char: str = "█", empty_char: str = "░"):
    try:
        if width is None:
            width = _get_bar_width()
        if not isinstance(total, (int, float)) or total <= 0:
            bar = empty_char * width
            return f"[dim]{bar}[/] N/A"
        used_clamped = max(0, min(used, total))
        frac = used_clamped / total
        filled = int(round(frac * width))
        filled_part = fill_char * filled
        empty_part = empty_char * (width - filled)
        pct = int(round(frac * 100))
        if pct >= 50:
            color = "neon_green"
        elif pct >= 20:
            color = "neon_yellow"
        else:
            color = "red"
        return f"[{color}]{filled_part}[/][dim]{empty_part}[/] {pct}%"
    except Exception:
        bar = empty_char * (width or 12)
        return f"[dim]{bar}[/] 0%"

# --------------------------
# package detail / family / my packages functions
# --------------------------
def show_package_details(api_key, tokens, package_option_code, is_enterprise, option_order = -1):
    active_user = AuthInstance.active_user
    subscription_type = active_user.get("subscription_type", "") if active_user else ""
    
    clear_screen()

    with loading_animation("Fetching package details..."):
        package = get_package(api_key, tokens, package_option_code)

    if not package:
        console.print("[error]Failed to load package details.[/]")
        pause()
        return False

    price = package["package_option"].get("price", "")
    detail = display_html(package["package_option"].get("tnc", ""))
    validity = package["package_option"].get("validity", "")

    option_name = package.get("package_option", {}).get("name","")
    family_name = package.get("package_family", {}).get("name","")
    variant_name = package.get("package_detail_variant", "").get("name","")
    
    title = f"{family_name} - {variant_name} - {option_name}".strip()
    
    parent_code = package.get("package_addon", {}).get("parent_code","")
    if parent_code == "":
        parent_code = "N/A"
    
    token_confirmation = package.get("token_confirmation", "")
    ts_to_sign = package.get("timestamp", "")
    payment_for = package.get("package_family", {}).get("payment_for", "")
    
    payment_items = [
        PaymentItem(
            item_code=package_option_code,
            product_type="",
            item_price=price,
            item_name=f"{variant_name} {option_name}".strip(),
            tax=0,
            token_confirmation=token_confirmation,
        )
    ]
    
    # Details Table
    details_table = Table(show_header=False, box=None, padding=(0, 1))
    details_table.add_column("Key", style="neon_cyan", justify="right")
    details_table.add_column("Value", style="bold white")

    details_table.add_row("Nama:", title)
    details_table.add_row("Harga:", f"Rp {price}")
    details_table.add_row("Payment For:", str(payment_for))
    details_table.add_row("Masa Aktif Paket:", str(validity))
    details_table.add_row("Point:", str(package.get('package_option', {}).get('point', "")))
    details_table.add_row("Plan Type:", package.get('package_family', {}).get('plan_type', ""))
    details_table.add_row("Kode Paket:", f"[neon_yellow]{package_option_code}[/]")
    details_table.add_row("Parent Code:", parent_code)

    activated_ts = (
        package.get("activated_at")
        or package.get("active_since")
        or package.get("package_option", {}).get("activated_at")
        or package.get("package_option", {}).get("active_since")
    )
    reset_ts = (
        package.get("reset_at")
        or package.get("reset_quota_at")
        or package.get("package_option", {}).get("reset_at")
        or package.get("package_option", {}).get("reset_quota_at")
    )

    if activated_ts:
        details_table.add_row("Masa Aktif Kuota:", _format_ts(activated_ts))
    if reset_ts:
        days_left = _days_until(reset_ts)
        if days_left is not None:
            details_table.add_row("Akhir Reset Kuota:", f"{_format_ts(reset_ts)} (sisa {days_left} hari)")
        else:
            details_table.add_row("Akhir Reset Kuota:", _format_ts(reset_ts))

    print_cyber_panel(details_table, title="DETAIL PAKET")

    benefits = package.get("package_option", {}).get("benefits", [])
    if benefits and isinstance(benefits, list):
        # compute bar width adaptively
        bar_width = _get_bar_width()

        benefit_table = Table(show_header=True, header_style="neon_pink", box=None)
        benefit_table.add_column("Benefit Name", style="white")
        benefit_table.add_column("Total/Quota", style="neon_green")
        benefit_table.add_column("Type", style="dim", width=12)
        benefit_table.add_column("Progress", style="neon_green")

        for benefit in benefits:
            total_display = ""
            data_type = benefit.get('data_type', '')
            remaining = benefit.get('remaining', benefit.get('total', 0)) or 0
            total = benefit.get('total', 0) or 0
            used = (total - remaining) if isinstance(total, (int, float)) else 0

            if data_type == "VOICE" and benefit.get('total', 0) > 0:
                total_display = f"{benefit.get('total', 0)/60:.2f} menit"
            elif data_type == "TEXT" and benefit.get('total', 0) > 0:
                total_display = f"{benefit.get('total', 0)} SMS"
            elif data_type == "DATA" and benefit.get('total', 0) > 0:
                quota = int(benefit.get('total', 0))
                if quota >= 1_000_000_000:
                    quota_gb = quota / (1024 ** 3)
                    total_display = f"{quota_gb:.2f} GB"
                elif quota >= 1_000_000:
                    quota_mb = quota / (1024 ** 2)
                    total_display = f"{quota_mb:.2f} MB"
                elif quota >= 1_000:
                    quota_kb = quota / 1024
                    total_display = f"{quota_kb:.2f} KB"
                else:
                    total_display = f"{quota} B"
            else:
                total_display = f"{remaining} / {total}"

            if benefit.get("is_unlimited", False):
                total_display = "Unlimited"
                progress = _render_progress_bar(0, 1, width=bar_width)
            else:
                progress = _render_progress_bar(used, total, width=bar_width)

            benefit_table.add_row(benefit.get('name', 'N/A'), total_display, data_type, progress)

        print_cyber_panel(benefit_table, title="BENEFITS")

    with loading_animation("Checking addons..."):
        addons = get_addons(api_key, tokens, package_option_code)

    console.print(Panel(detail or "No terms available.", title="[neon_pink]SnK MyXL[/]", border_style="dim white"))

    # Options loop
    in_package_detail_menu = True
    while in_package_detail_menu:
        menu_table = Table(show_header=False, box=None)
        menu_table.add_row("1", "Beli dengan Pulsa")
        menu_table.add_row("2", "Beli dengan E-Wallet")
        menu_table.add_row("3", "Bayar dengan QRIS")
        menu_table.add_row("4", "Pulsa + Decoy")
        menu_table.add_row("5", "Pulsa + Decoy V2")
        menu_table.add_row("6", "QRIS + Decoy (+1K)")
        menu_table.add_row("7", "QRIS + Decoy V2")
        menu_table.add_row("8", "Pulsa N kali")

        if payment_for == "":
            payment_for = "BUY_PACKAGE"
        
        if payment_for == "REDEEM_VOUCHER":
            menu_table.add_row("B", "Ambil sebagai bonus")
            menu_table.add_row("BA", "Kirim bonus")
            menu_table.add_row("L", "Beli dengan Poin")
        
        if option_order != -1:
            menu_table.add_row("0", "Tambah ke Bookmark")
        menu_table.add_row("00", "Kembali ke daftar paket")

        print_cyber_panel(menu_table, title="ACTIONS")

        choice = cyber_input("Pilihan")
        if choice == "00":
            return False
        elif choice == "0" and option_order != -1:
            success = BookmarkInstance.add_bookmark(
                family_code=package.get("package_family", {}).get("package_family_code",""),
                family_name=package.get("package_family", {}).get("name",""),
                is_enterprise=is_enterprise,
                variant_name=variant_name,
                option_name=option_name,
                order=option_order,
            )
            if success:
                console.print("[neon_green]Paket berhasil ditambahkan ke bookmark.[/]")
            else:
                console.print("[warning]Paket sudah ada di bookmark.[/]")
            pause()
            continue
        
        # implement other menu choices as before...
        elif choice == '1':
            settlement_balance(
                api_key,
                tokens,
                payment_items,
                payment_for,
                True
            )
            pause()
            return True
        elif choice == '2':
            show_multipayment(
                api_key,
                tokens,
                payment_items,
                payment_for,
                True,
            )
            pause()
            return True
        elif choice == '3':
            show_qris_payment(
                api_key,
                tokens,
                payment_items,
                payment_for,
                True,
            )
            pause()
            return True
        elif choice == '8':
            use_decoy_for_n_times = cyber_input("Use decoy package? (y/n)").strip().lower() == 'y'
            n_times_str = cyber_input("Enter number of times to purchase (e.g., 3)").strip()
            delay_seconds_str = cyber_input("Enter delay between purchases in seconds (e.g., 25)").strip()
            if not delay_seconds_str.isdigit():
                delay_seconds_str = "0"
            try:
                n_times = int(n_times_str)
                if n_times < 1:
                    raise ValueError("Number must be at least 1.")
            except ValueError:
                console.print("[error]Invalid number entered. Please enter a valid integer.[/]")
                pause()
                continue
            purchase_n_times_by_option_code(
                n_times,
                option_code=package_option_code,
                use_decoy=use_decoy_for_n_times,
                delay_seconds=int(delay_seconds_str),
                pause_on_success=False,
                token_confirmation_idx=1
            )
        else:
            console.print("[warning]Purchase cancelled or unrecognized option.[/]")
            return False
    pause()
    sys.exit(0)

def get_packages_by_family(
    family_code: str,
    is_enterprise: bool | None = None,
    migration_type: str | None = None
):
    api_key = AuthInstance.api_key
    tokens = AuthInstance.get_active_tokens()
    if not tokens:
        console.print("[error]No active user tokens found.[/]")
        pause()
        return None
    
    packages = []
    
    with loading_animation("Fetching family packages..."):
        data = get_family(
            api_key,
            tokens,
            family_code,
            is_enterprise,
            migration_type
        )
    
    if not data:
        console.print("[error]Failed to load family data.[/]")
        pause()
        return None

    price_currency = "Rp"
    rc_bonus_type = data["package_family"].get("rc_bonus_type", "")
    if rc_bonus_type == "MYREWARDS":
        price_currency = "Poin"
    
    in_package_menu = True
    while in_package_menu:
        clear_screen()

        # Family Info Panel
        family_table = Table(show_header=False, box=None)
        family_table.add_column("Key", style="neon_cyan", justify="right")
        family_table.add_column("Value", style="bold white")

        family_table.add_row("Family Name:", data['package_family']['name'])
        family_table.add_row("Family Code:", family_code)
        family_table.add_row("Family Type:", data['package_family']['package_family_type'])
        family_table.add_row("Variant Count:", str(len(data['package_variants'])))

        print_cyber_panel(family_table, title="FAMILY INFO")

        # Packages List
        pkg_table = Table(show_header=True, header_style="neon_pink", box=None, padding=(0, 1))
        pkg_table.add_column("No", style="neon_green", justify="right", width=4)
        pkg_table.add_column("Package Name", style="bold white")
        pkg_table.add_column("Price", style="yellow")
        
        package_variants = data["package_variants"]
        
        option_number = 1
        packages = []
        
        for variant in package_variants:
            variant_name = variant["name"]
            for option in variant["package_options"]:
                option_name = option["name"]
                price_display = f"{price_currency} {option['price']}"
                full_name = f"{variant_name} - {option_name}"
                packages.append({
                    "number": option_number,
                    "variant_name": variant_name,
                    "option_name": option_name,
                    "price": option["price"],
                    "code": option["package_option_code"],
                    "option_order": option["order"]
                })
                pkg_table.add_row(str(option_number), full_name, price_display)
                option_number += 1

        print_cyber_panel(pkg_table, title="AVAILABLE PACKAGES")

        console.print("[dim]00. Kembali ke menu utama[/]")
        pkg_choice = cyber_input("Pilih paket (nomor)")
        if pkg_choice == "00":
            in_package_menu = False
            return None
        
        if isinstance(pkg_choice, str) == False or not pkg_choice.isdigit():
            console.print("[error]Input tidak valid. Silakan masukan nomor paket.[/]")
            pause()
            continue
        
        selected_pkg = next((p for p in packages if p["number"] == int(pkg_choice)), None)
        
        if not selected_pkg:
            console.print("[error]Paket tidak ditemukan. Silakan masukan nomor yang benar.[/]")
            pause()
            continue
        
        show_package_details(
            api_key,
            tokens,
            selected_pkg["code"],
            is_enterprise,
            option_order=selected_pkg["option_order"],
        )
        
    return packages

def fetch_my_packages():
    """
    Keep compatibility with main.py import.
    This is a wrapper that reuses the logic in this module (calls API, displays).
    """
    in_my_packages_menu = True
    while in_my_packages_menu:
        api_key = AuthInstance.api_key
        tokens = AuthInstance.get_active_tokens()
        if not tokens:
            console.print("[error]No active user tokens found.[/]")
            pause()
            return None
        
        id_token = tokens.get("id_token")
        
        path = "api/v8/packages/quota-details"
        
        payload = {
            "is_enterprise": False,
            "lang": "en",
            "family_member_id": ""
        }
        
        with loading_animation("Fetching my packages..."):
            res = send_api_request(api_key, path, payload, id_token, "POST")

        if res.get("status") != "SUCCESS":
            console.print("[error]Failed to fetch packages[/]")
            console.print_json(data=res)
            pause()
            return None
        
        quotas = res["data"].get("quotas", [])
        
        clear_screen()

        # --- Paket Aktif header centered ---
        try:
            active_user = AuthInstance.get_active_user() or {}
            account_number = active_user.get("number", "N/A")
            account_name = active_user.get("name", "") or ""
        except Exception:
            account_number = "N/A"
            account_name = ""

        header = f"Akun aktif: {account_number}"
        if account_name:
            header = f"{header}  •  {account_name}"
        header_panel = Panel(Align(header, align="center"), title="PAKET AKTIF", border_style="neon_cyan")
        console.print(header_panel)
        # --- end header ---

        # reuse listing logic (same as earlier get_packages_by_family display of quotas)
        # build and display as before...
        # (For brevity, call the same listing logic as implemented earlier)
        # We'll display each quota with details & benefits (same rendering as fetch_my_packages earlier)
        # To avoid duplication, we can reuse the code above (here it's already implemented in fetch_my_packages).
        # For simplicity display minimal view then loop to display details on selection.

        my_packages = []
        num = 1

        if not quotas:
            console.print("[warning]No packages found.[/]")
            pause()
            return None

        # show compact list with name and summary
        main_table = Table(show_header=True, header_style="neon_pink", box=None)
        main_table.add_column("No", style="neon_green", justify="right", width=4)
        main_table.add_column("Package Name", style="bold white")
        main_table.add_column("Summary", style="cyan")

        for quota in quotas:
            quota_code = quota.get("quota_code", "")
            quota_name = quota.get("name", "")
            benefits = quota.get("benefits", [])
            summary = "No benefits"
            if benefits:
                b = benefits[0]
                data_type = b.get("data_type", "")
                remaining = b.get("remaining", 0)
                total = b.get("total", 0)
                if data_type == "DATA":
                    summary = f"{format_quota_byte(remaining)} / {format_quota_byte(total)}"
                elif data_type == "VOICE":
                    summary = f"{remaining/60:.1f}m / {total/60:.1f}m"
                else:
                    summary = f"{remaining} / {total} {data_type}"
                if len(benefits) > 1:
                    summary += f" (+{len(benefits)-1} more)"

            main_table.add_row(str(num), quota_name, summary)
            my_packages.append({
                "number": num,
                "name": quota_name,
                "quota_code": quota_code,
                "full_data": quota
            })
            num += 1

        print_cyber_panel(main_table, title="MY PACKAGES")

        console.print(Panel(
            """[bold white]Input Number[/]: View Detail
[bold white]del <N>[/]: Unsubscribe
[bold white]00[/]: Back to Main Menu""",
            title="ACTIONS",
            border_style="neon_cyan"
        ))

        choice = cyber_input("Choice")
        if choice == "00":
            in_my_packages_menu = False

        if choice.isdigit() and int(choice) > 0 and int(choice) <= len(my_packages):
            selected_pkg = next((pkg for pkg in my_packages if pkg["number"] == int(choice)), None)
            if not selected_pkg:
                console.print("[error]Paket tidak ditemukan. Silakan masukan nomor yang benar.[/]")
                pause()
                continue
            _ = show_package_details(api_key, tokens, selected_pkg["quota_code"], False)
        elif choice.startswith("del "):
            parts = choice.split(" ")
            if len(parts) != 2 or not parts[1].isdigit():
                console.print("[error]Invalid input for delete command.[/]")
                pause()
                continue
            del_number = int(parts[1])
            del_pkg = next((pkg for pkg in my_packages if pkg["number"] == del_number), None)
            if not del_pkg:
                console.print("[error]Package not found for deletion.[/]")
                pause()
                continue
            confirm = cyber_input(f"Are you sure you want to unsubscribe from package  {del_number}. {del_pkg['name']}? (y/n)")
            if confirm.lower() == 'y':
                with loading_animation(f"Unsubscribing from {del_pkg['name']}..."):
                    success = unsubscribe(
                        api_key,
                        tokens,
                        del_pkg["quota_code"],
                        del_pkg.get("product_subscription_type",""),
                        del_pkg.get("product_domain","")
                    )
                if success:
                    console.print("[neon_green]Successfully unsubscribed from the package.[/]")
                else:
                    console.print("[error]Failed to unsubscribe from the package.[/]")
            else:
                console.print("[warning]Unsubscribe cancelled.[/]")
            pause()
    return None
