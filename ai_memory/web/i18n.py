"""Translation layer for the admin web.

The source language is ENGLISH — English strings live directly in the code and double
as the lookup keys. Czech is the dictionary below. A missing translation falls back to
English (nothing disappears), so new strings can be added without touching this file.
"""
from __future__ import annotations

from contextvars import ContextVar

DEFAULT_LANG = "en"
LANGS = {"en": "English", "cs": "Čeština"}
_lang: ContextVar[str] = ContextVar("lang", default=DEFAULT_LANG)

CS = {
    # settings: scheduled export, remembered passphrase, network exposure
    "Settings": "Nastaven\u00ed",
    "Scheduled export": "Pl\u00e1novan\u00fd export",
    "Export automatically": "Exportovat automaticky",
    "Destination folder": "C\u00edlov\u00e1 slo\u017eka",
    "e.g. a mounted share": "nap\u0159. p\u0159ipojen\u00fd s\u00ed\u0165ov\u00fd disk",
    "Every (hours)": "Jak \u010dasto (hodin)",
    "Keep copies": "Kolik kopi\u00ed nechat",
    "Export now": "Exportovat te\u010f",
    "No backup has ever been taken.": "Zat\u00edm nebyla po\u0159\u00edzena \u017e\u00e1dn\u00e1 z\u00e1loha.",
    "The last backup is {n} days old": "Posledn\u00ed z\u00e1loha je {n} dn\u00ed star\u00e1",
    "The last backup is {n} hours old": "Posledn\u00ed z\u00e1loha je {n} hodin star\u00e1",
    "Set a destination folder below and export once, so a restore is something you have done rather than something you hope works.": "Nastav n\u00ed\u017ee c\u00edlovou slo\u017eku a jednou exportuj, a\u0165 je obnova n\u011bco, co jsi u\u017e zkusil, a ne n\u011bco, o \u010dem douf\u00e1\u0161, \u017ee funguje.",
    "The schedule only runs while this admin web is up, which needs the passphrase \u2014 so days the memory is never unlocked are days with no backup.": "Pl\u00e1n b\u011b\u017e\u00ed jen tehdy, kdy\u017e b\u011b\u017e\u00ed tento admin web, a ten pot\u0159ebuje heslo \u2014 dny, kdy pam\u011b\u0165 nikdo neodemkne, jsou dny bez z\u00e1lohy.",
    "After a change, wait (minutes)": "Po zm\u011bn\u011b po\u010dkat (minut)",
    "A copy is also made whenever the memory changes, once this long has passed since the last one. The wait is what stops a busy session from filling the whole retention with one copy per write.": "Kopie vznik\u00e1 tak\u00e9 poka\u017ed\u00e9, kdy\u017e se pam\u011b\u0165 zm\u011bn\u00ed \u2014 jakmile od t\u00e9 posledn\u00ed uplyne tahle doba. To \u010dek\u00e1n\u00ed je tu proto, aby ru\u0161n\u00e1 pr\u00e1ce nezaplnila celou retenci jednou kopi\u00ed na ka\u017ed\u00fd z\u00e1pis.",
    "With this on, a copy is made whenever the memory changes \u2014 once the wait below has passed since the last one. Nothing changes, nothing is copied.": "Kdy\u017e je tohle zapnut\u00e9, kopie vznik\u00e1 poka\u017ed\u00e9, kdy\u017e se pam\u011b\u0165 zm\u011bn\u00ed \u2014 jakmile od t\u00e9 posledn\u00ed uplyne \u010dek\u00e1n\u00ed n\u00ed\u017ee. Nic se nezm\u011bn\u00ed, nic se nekop\u00edruje.",
    "The wait is what stops a busy session from filling the whole retention with one copy per write.": "\u010cek\u00e1n\u00ed je tu proto, aby ru\u0161n\u00e1 pr\u00e1ce nezaplnila celou retenci jednou kopi\u00ed na ka\u017ed\u00fd z\u00e1pis.",
    "Also copy on a schedule, even with nothing changed": "Z\u00e1lohovat i pravideln\u011b, i kdy\u017e se nic nezm\u011bnilo",
    "Recommended: it is the only copy that would still be made if change detection ever went wrong. Turn it off to stop identical copies accumulating on quiet days.": "Doporu\u010deno: je to jedin\u00e1 kopie, kter\u00e1 by vznikla i tehdy, kdyby detekce zm\u011bn selhala. Vypni to, pokud nechce\u0161, aby se v klidn\u00fdch dnech hromadily identick\u00e9 kopie.",
    "Autostart": "Spou\u0161t\u011bn\u00ed po p\u0159ihl\u00e1\u0161en\u00ed",
    "Start the memory when I log in": "Spustit pam\u011b\u0165 po p\u0159ihl\u00e1\u0161en\u00ed",
    "Without this the memory stays locked until you start it yourself.": "Bez toho z\u016fstane pam\u011b\u0165 zam\u010den\u00e1, dokud ji s\u00e1m nespust\u00ed\u0161.",
    "Show the tray icon when I log in": "Zobrazit ikonu v panelu po p\u0159ihl\u00e1\u0161en\u00ed",
    "The icon only reports the state; it never starts the memory on its own.": "Ikona jen ukazuje stav; pam\u011b\u0165 sama nikdy nespust\u00ed.",
    "Download a copy": "St\u00e1hnout kopii",
    "Last export": "Posledn\u00ed export",
    "Last export failed": "Posledn\u00ed export selhal",
    "Nothing exported yet.": "Zat\u00edm se nic neexportovalo.",
    "The export failed.": "Export selhal.",
    "The copy did not verify": "Kopie nepro\u0161la ov\u011b\u0159en\u00edm",
    "The interval and the number of copies must be numbers.":
        "Interval a po\u010det kopi\u00ed mus\u00ed b\u00fdt \u010d\u00edsla.",
    "The export is the ENCRYPTED database, exactly as it sits on disk -- not a "
    "readable dump. It carries no passphrase, so it is safe to write to a network "
    "share or ship offsite; without the passphrase it cannot be read. Point the "
    "folder at a share and your existing backup handles the rest.":
        "Export je \u0160IFROVAN\u00c1 datab\u00e1ze p\u0159esn\u011b tak, jak le\u017e\u00ed na disku \u2014 ne \u010diteln\u00fd "
        "v\u00fdpis. Heslo v n\u011bm nen\u00ed, tak\u017ee se d\u00e1 bez obav zapsat na s\u00ed\u0165ov\u00fd disk nebo "
        "poslat mimo d\u016fm; bez hesla je ne\u010diteln\u00e1. Nasm\u011bruj slo\u017eku na share a o zbytek "
        "se postar\u00e1 tvoje st\u00e1vaj\u00edc\u00ed z\u00e1lohov\u00e1n\u00ed.",
    "An overdue export runs as soon as the application is next started, so a "
    "machine that was switched off does not simply skip a day.":
        "Zme\u0161kan\u00fd export se spust\u00ed hned p\u0159i p\u0159\u00ed\u0161t\u00edm startu aplikace, tak\u017ee vypnut\u00fd "
        "po\u010d\u00edta\u010d neznamen\u00e1 vynechan\u00fd den.",
    "Master passphrase": "Hlavn\u00ed heslo",
    "master passphrase": "hlavn\u00ed heslo",
    "Remember on this machine": "Zapamatovat na tomto po\u010d\u00edta\u010di",
    "Forget it and ask me again": "Zapomenout a p\u0159\u00ed\u0161t\u011b se zeptat",
    "That is not the master passphrase.": "To nen\u00ed hlavn\u00ed heslo.",
    "Could not store it": "Nepoda\u0159ilo se ulo\u017eit",
    "The passphrase is remembered in the OS credential store, and the application "
    "starts unattended.":
        "Heslo je ulo\u017een\u00e9 v \u00falo\u017ei\u0161ti p\u0159ihla\u0161ovac\u00edch \u00fadaj\u016f syst\u00e9mu a aplikace "
        "startuje bez ptan\u00ed.",
    "This machine has no credential store, so the passphrase cannot be remembered "
    "here.":
        "Tento po\u010d\u00edta\u010d nem\u00e1 \u00falo\u017ei\u0161t\u011b p\u0159ihla\u0161ovac\u00edch \u00fadaj\u016f, heslo tu ulo\u017eit nelze.",
    "Remembering it means anything running as your user account can read it back, "
    "so the encryption stops protecting THIS machine. It keeps protecting every "
    "copy elsewhere -- the share, the offsite backup, a stolen disk. Enter the "
    "passphrase to confirm.":
        "Zapamatov\u00e1n\u00ed znamen\u00e1, \u017ee si ho p\u0159e\u010dte cokoli b\u011b\u017e\u00edc\u00edho pod tv\u00fdm \u00fa\u010dtem, "
        "tak\u017ee \u0161ifrov\u00e1n\u00ed p\u0159estane chr\u00e1nit TENHLE po\u010d\u00edta\u010d. Ostatn\u00ed kopie chr\u00e1n\u00ed d\u00e1l "
        "\u2014 share, z\u00e1lohu mimo d\u016fm, ukraden\u00fd disk. Potvr\u010f zad\u00e1n\u00edm hesla.",
    "Network access": "P\u0159\u00edstup po s\u00edti",
    "Reachable on the network: MCP server": "Dostupn\u00e9 po s\u00edti: MCP server",
    "Reachable on the network: this admin web": "Dostupn\u00e9 po s\u00edti: tato spr\u00e1va",
    "Changing this takes effect when the application is restarted.":
        "Zm\u011bna se projev\u00ed po restartu aplikace.",
    "To open the port in the firewall, as administrator":
        "Otev\u0159en\u00ed portu ve firewallu, jako spr\u00e1vce",
    "By default both servers listen only on this machine. The bind address is what "
    "decides reachability -- a firewall rule alone changes nothing.":
        "Ve v\u00fdchoz\u00edm stavu oba servery poslouchaj\u00ed jen na tomto po\u010d\u00edta\u010di. O dostupnosti "
        "rozhoduje adresa, na kterou se v\u00e1\u017eou \u2014 samotn\u00e9 pravidlo ve firewallu nezm\u011bn\u00ed nic.",
    "The MCP server authenticates each agent with its own revocable key, so "
    "exposing it is a bounded decision -- a leaked key is one agent and can be "
    "rotated. Open the port in your firewall as well; that needs administrator "
    "rights, so it is not done for you.":
        "MCP server ov\u011b\u0159uje ka\u017ed\u00e9ho agenta jeho vlastn\u00edm odvolateln\u00fdm kl\u00ed\u010dem, tak\u017ee "
        "vystaven\u00ed m\u00e1 omezen\u00fd dosah \u2014 uniknut\u00fd kl\u00ed\u010d je jeden agent a d\u00e1 se vym\u011bnit. "
        "Otev\u0159i i port ve firewallu; to chce pr\u00e1va spr\u00e1vce, proto se to ned\u011bl\u00e1 automaticky.",
    "DANGER. This admin web logs in with the MASTER PASSPHRASE over plain HTTP. "
    "On a network that passphrase -- which unlocks every record and every agent "
    "key -- travels in cleartext and can be read off the wire. Only do this "
    "behind a reverse proxy that terminates TLS.":
        "POZOR. Tato spr\u00e1va se p\u0159ihla\u0161uje HLAVN\u00cdM HESLEM p\u0159es nezabezpe\u010den\u00e9 HTTP. "
        "Po s\u00edti by to heslo \u2014 kter\u00e9 odemyk\u00e1 v\u0161echny z\u00e1znamy i v\u0161echny kl\u00ed\u010de agent\u016f \u2014 "
        "putovalo v otev\u0159en\u00e9m tvaru a d\u00e1 se odposlechnout. D\u011blej to jen za reverzn\u00ed "
        "proxy, kter\u00e1 ukon\u010duje TLS.",
    # header and navigation
    "ai-memory admin": "ai-memory admin",
    "Overview": "Přehled",
    "Memory": "Paměť",
    "Password": "Heslo",
    "Log out": "Odhlásit",
    # sign in
    "Sign in": "Přihlášení",
    "Sign in ": "Přihlásit",  # button (the trailing space distinguishes it from the heading)
    "Wrong passphrase.": "Špatná passphrase.",
    "Master passphrase:": "Master passphrase:",
    # dashboard — agents
    "Agents and keys": "Agenti a klíče",
    "Agent": "Agent",
    "Permissions": "Práva",
    "Created": "Vytvořen",
    "Rotate key": "Rotovat klíč",
    "Delete": "Smazat",
    "Delete agent": "Smazat agenta",
    "(none)": "(žádní)",
    "New agent": "Nový agent",
    "Name:": "Název:",
    "(default permissions: own=rw, shared=rw, others=r)":
        "(výchozí práva: vlastní=rw, shared=rw, ostatní=r)",
    "Create + issue key": "Vytvořit + vydat klíč",
    # dashboard — permission matrix
    "R": "Č",
    "W": "Z",
    "inherited from *": "zděděno z *",
    "= read": "= čtení",
    "= write (turning on write turns on read too)":
        "= zápis (zapnutím zápisu se zapne i čtení)",
    "column is the default permission for namespaces without their own setting; "
    "dimmed cells inherit from it.":
        "je výchozí právo pro namespacy bez vlastního nastavení; zesvětlené buňky se z něj dědí.",
    "Save permissions": "Uložit práva",
    "Set a single permission manually (also for a namespace that does not exist yet)":
        "Nastavit jedno právo ručně (i pro namespace, který ještě neexistuje)",
    "agent": "agent",
    "on namespace": "na namespace",
    "Save": "Uložit",
    # dashboard — namespaces
    "Namespaces": "Namespacy",
    "Namespace": "Namespace",
    "Records": "Počet záznamů",
    "(empty so far)": "(zatím prázdné)",
    # keys
    "created": "vytvořen",
    "API key (shown only now — save it and put it into the client):":
        "API klíč (zobrazí se jen teď — ulož si ho a vlož do klienta):",
    "Back to overview": "Zpět na přehled",
    "New key for": "Nový klíč pro",
    "The old key stopped working. Update the client.":
        "Starý klíč přestal platit. Aktualizuj klienta.",
    "Back": "Zpět",
    "Key rotation": "Rotace klíče",
    # errors
    "Error": "Chyba",
    "CSRF token mismatch.": "CSRF token nesouhlasí.",
    "back": "zpět",
    "Changes saved:": "Uloženo změn:",
    # password change
    "Change master passphrase": "Změna master passphrase",
    "Current password:": "Současné heslo:",
    "New password:": "Nové heslo:",
    "New password again:": "Nové heslo znovu:",
    "Change password": "Změnit heslo",
    "Password change": "Změna hesla",
    "Data is not re-encrypted (fast). Running services keep working; the new password "
    "applies from the next start. Losing the password = irreversible loss of the memory.":
        "Data se nepřešifrovávají (rychlé). Běžící služby fungují dál; nové heslo platí "
        "od příštího startu. Ztráta hesla = nevratná ztráta paměti.",
    "Current password does not match.": "Současné heslo nesouhlasí.",
    "New password must be at least 8 characters.": "Nové heslo musí mít aspoň 8 znaků.",
    "New passwords do not match.": "Nová hesla se neshodují.",
    "Password changed": "Heslo změněno",
    "Use the new password when the services start next time. Running services keep going unchanged.":
        "Při příštím startu služeb použij nové heslo. Běžící služby jedou dál beze změny.",
    # memory browser
    "Memory: all": "Paměť: vše",
    "Memory:": "Paměť:",
    "all": "vše",
    "name": "název",
    "description": "popis",
    "type": "typ",
    "updated": "změněno",
    "by": "kým",
    "(nothing)": "(nic)",
    "type ": "typ ",
    "tags": "tagy",
    "author": "autor",
    "last": "naposledy",
    "at": "v",
    "History": "Historie",
    "when": "kdy",
    "action": "akce",
    "who": "kdo",
    # filtering
    "Memory: filtered": "Paměť: filtrovaná",
    "namespace": "namespace",
    "tag": "tag",
    "(any)": "(cokoliv)",
    "(no tags)": "(bez tagů)",
    "Filter": "Filtrovat",
    "clear filters": "zrušit filtry",
    "{n} of {total} records": "{n} z {total} záznamů",
    # tag administration
    "Tags": "Tagy",
    "Manage tags": "Spravovat tagy",
    "records": "záznamů",
    "actions": "akce",
    "new name": "nový název",
    "Rename": "Přejmenovat",
    "Remove this tag from all records?": "Odebrat tento tag ze všech záznamů?",
    "Both the old and the new tag name are required.":
        "Je potřeba zadat starý i nový název tagu.",
    "Renaming onto an existing tag merges the two. Deleting removes the tag "
    "from every record; the records themselves stay.":
        "Přejmenování na existující tag oba sloučí. Smazání odebere tag ze všech "
        "záznamů; samotné záznamy zůstanou.",
    # memory list: expanding a row, search, per-record sharing
    "search…": "hledat…",
    "show detail": "zobrazit detail",
    "visible to": "vidí",
    "Attachments": "Přílohy",
    "Nothing attached yet.": "Zatím nic přiloženo.",
    "Attach": "Přiložit",
    "what it shows (optional)": "co je na tom (nepovinné)",
    "Delete this attachment?": "Smazat tuto přílohu?",
    "No file chosen.": "Nebyl vybrán soubor.",
    "file": "soubor",
    "size": "velikost",
    "added": "přidáno",
    "checked": "ověřeno",
    "never confirmed": "nikdy nepotvrzeno",
    "days ago": "dní zpět",
    "never — nobody has confirmed this record against reality":
        "nikdy — nikdo tento záznam neověřil proti skutečnosti",
    "nobody": "nikdo",
    "{n} agents": "{n} agentů",
    "everyone allowed by the namespace ACL": "všichni podle práv namespace",
    "nobody — the record is hidden from every agent":
        "nikdo — záznam je skrytý všem agentům",
    "Open and edit →": "Otevřít a upravit →",
    # record editor
    "Saved.": "Uloženo.",
    "(comma separated)": "(oddělené čárkou)",
    "content": "obsah",
    "(markdown)": "(markdown)",
    "Save changes": "Uložit změny",
    "Who may see this record": "Kdo smí tento záznam vidět",
    "Anyone allowed by the namespace permissions (default)":
        "Kdokoliv podle práv namespace (výchozí)",
    "Only the agents ticked below": "Jen zaškrtnutí agenti",
    "(no agents yet)": "(zatím žádní agenti)",
    "This narrows the namespace permissions, it never widens them. "
    "Only you can change it — no agent can grant itself access.":
        "Zužuje to práva namespace, nikdy nerozšiřuje. Měnit to můžeš jen ty — "
        "žádný agent si přístup sám nepřidá.",
    "Too many wrong passphrases. Try again in {n} s.":
        "Příliš mnoho špatných hesel. Zkus to znovu za {n} s.",
    # settings: the read-only installation block
    "Installation": "Instalace",
    "Version": "Verze",
    "portable (everything in the data folder beside the program)":
        "portable (vše ve složce data vedle programu)",
    "installed build": "nainstalovaný build",
    "running from a source checkout": "běží ze zdrojáků",
    "Program": "Program",
    "Data": "Data",
    "Config": "Konfigurace",
    "Database": "Databáze",
    "Embedding model": "Embedding model",
    "Attachment read-out (decrypted copies)": "Výdej příloh (rozšifrované kopie)",
    "Backup script destination": "Cíl zálohovacího skriptu",
    "set by": "nastaveno přes",
    "Attachments themselves are stored encrypted inside the database and are in every "
    "backup with it; the read-out folder only holds copies handed to agents.":
        "Samotné přílohy jsou uložené zašifrované v databázi a jsou s ní v každé záloze; "
        "složka výdeje drží jen kopie předané agentům.",
    # settings: network, access and HTTPS (1.2.0)
    "Administration": "Administrace",
    "Your address": "Tvoje adresa",
    "Network and access": "Síť a přístup",
    "Your request came from": "Tvůj požadavek přišel z",
    "directly, no proxy": "přímo, bez proxy",
    "through the trusted proxy {p}": "přes důvěryhodnou proxy {p}",
    "Listen on address": "Poslouchat na adrese",
    "Port": "Port",
    "Allowed host names": "Povolené názvy hostitele",
    "The names clients use to reach this memory. localhost and 127.0.0.1 always work; any other name must be listed, which stops DNS-rebinding attacks.":
        "Jména, přes která se klienti k paměti připojují. localhost a 127.0.0.1 fungují vždy; každé jiné jméno musí být v seznamu, což brání útokům typu DNS rebinding.",
    "MCP: allowed IPs (optional)": "MCP: povolené IP (volitelné)",
    "anywhere -- the agent token protects it": "odkudkoli -- chrání to token agenta",
    "Admin web: allowed IPs (required)": "Administrace: povolené IP (povinné)",
    "IP addresses or ranges, comma separated (192.168.1.0/24, 10.8.0.5). This machine itself can always reach the admin web.":
        "IP adresy nebo rozsahy oddělené čárkou (192.168.1.0/24, 10.8.0.5). Tento počítač se do administrace dostane vždy.",
    "Trusted proxies": "Důvěryhodné proxy",
    "none": "žádná",
    "Only from these addresses is X-Forwarded-For believed -- set it to your reverse proxy, or every client looks like the proxy.":
        "Jen od těchto adres se věří hlavičce X-Forwarded-For -- nastav sem svou reverse proxy, jinak budou všichni klienti vypadat jako ona.",
    "Accept the agent key in the URL (?key=)": "Přijímat klíč agenta v URL (?key=)",
    "Only for clients that cannot send an Authorization header (LM Studio). The key then shows up in proxy logs.":
        "Jen pro klienty, kteří neumí poslat hlavičku Authorization (LM Studio). Klíč se pak objevuje v logu proxy.",
    "Address and port take effect after a restart; the lists apply immediately.":
        "Adresa a port platí po restartu; seznamy platí okamžitě.",
    "The port must be between 1 and 65535.": "Port musí být mezi 1 a 65535.",
    "The admin web needs at least one allowed address or range.":
        "Administrace potřebuje aspoň jednu povolenou adresu nebo rozsah.",
    "Encryption in the application": "Šifrování přímo v aplikaci",
    "off (plain HTTP / TLS proxy in front)": "vypnuto (HTTP / TLS proxy před aplikací)",
    "self-signed certificate": "self-signed certifikát",
    "my own certificate": "vlastní certifikát",
    "Certificate": "Certifikát",
    "self-signed": "self-signed",
    "issued by a CA": "vydaný certifikační autoritou",
    "valid until": "platný do",
    "No certificate yet.": "Zatím žádný certifikát.",
    "Generate a new self-signed certificate": "Vygenerovat nový self-signed certifikát",
    "Download the certificate": "Stáhnout certifikát",
    "Certificate (PEM, chain allowed)": "Certifikát (PEM, i s řetězcem)",
    "Private key (PEM, unencrypted)": "Privátní klíč (PEM, nešifrovaný)",
    "Upload my own certificate": "Nahrát vlastní certifikát",
    "A new certificate or mode takes effect after a restart. Clients must trust a self-signed certificate (Claude Code: NODE_EXTRA_CA_CERTS).":
        "Nový certifikát nebo režim platí po restartu. Klienti musí self-signed certifikátu důvěřovat (Claude Code: NODE_EXTRA_CA_CERTS).",
    "Unknown TLS mode.": "Neznámý režim TLS.",
    "Upload a certificate and key first.": "Nejdřív nahraj certifikát a klíč.",
    "Both files are needed.": "Jsou potřeba oba soubory.",
    "Invalid request (CSRF).": "Neplatný požadavek (CSRF).",
}


def set_lang(code: str | None) -> None:
    _lang.set(code if code in LANGS else DEFAULT_LANG)


def get_lang() -> str:
    return _lang.get()


def t(text: str) -> str:
    """Translate into the current language; without a translation returns the original (English)."""
    return CS.get(text, text) if _lang.get() == "cs" else text
