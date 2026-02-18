# E-klase Family (Home Assistant)

Home Assistant pielāgota integrācija, kas nolasa **E-klase Family** dienasgrāmatu un izveido **kalendāra ierakstus** pa stundām.

Kalendāra notikumu nosaukumos tiek pievienoti marķieri:
- **MD** – ja stundai ir mājasdarbs (`homeTasks`)
- **PD** – ja stundai ir pārbaudes darbs (`scheduledTests`)

Notikuma **Description** laukā tiek ielikts:
- skolēna profils (vārds, klase/skola)
- MD/PD teksts (vienkāršots no HTML uz plain text)

> ⚠️ Integrācija izmanto E-klase autentifikāciju ar lietotājvārdu/paroli. Glabājas Home Assistant config entry (HA storage), nevis `configuration.yaml`.

---

## Funkcijas

- 📅 Kalendārs ar stundu grafiku (no E-klase diary)
- 🔁 Automātiska atjaunošana pēc konfigurējama intervāla
- 🌙 Nakts režīms: no **00:00 līdz 06:00** refresh tiek izlaists
- 👀 “Watch” režīms: rēķina checksum šodienai/rītdienai (vai N dienām) pēc `lastModification.timeModified`, lai varētu triggerēt automatizācijas, ja stundu saraksts ir mainīts

---

## Instalēšana

### Variants A — HACS (ieteicamais)

1. Atver **HACS → Integrations**
2. Augšā pa labi: **⋮ → Custom repositories**
3. Pievieno repozitoriju:
   - **Repository:** `https://github.com/juvies/eklase`
   - **Category:** `Integration`
4. HACS → atver šo integrāciju → **Download**
5. **Restart Home Assistant**
6. Home Assistant → **Settings → Devices & services → Add integration**
7. Meklē: **E-klase Family** (vai integrācijas nosaukumu)

---

### Variants B — manuāli (bez HACS)

1. Nokopē integrācijas mapi uz: /config/custom_components/eklase_family/
(t.i., repo mapē jābūt `custom_components/eklase_family/...`)

2. **Restart Home Assistant**

3. Home Assistant → **Settings → Devices & services → Add integration** → meklē integrāciju

---

## Konfigurēšana (UI)

Pievienojot integrāciju, tiks prasīts:
- **Username / Password**
- **Refresh interval** (minūtēs)
- **Watch days** (cik dienas no šodienas skatīties izmaiņām; piem. 2 = šodien + rīt)
- Stundu laiki (l1_start/l1_end ...)

Pēc saglabāšanas integrācija pārlādēsies automātiski.

---

## Kalendāra notikumu formāts
**Summary piemērs:**
Anna - Angļu valoda - MD (110)
**Description piemērs:**
Ann Bērziņa — 8.d, Rīgas 431. vidusskola • MD: … • PD: …

---

## Entītijas un atribūti

Integrācija izveido `calendar` entītiju.

Papildus atribūti (`extra_state_attributes`):
- `last_refresh` – pēdējā refresh laiks (ISO)
- `watch_days` – cik dienas tiek “watch-ots”
- `watch_from` – sākuma datums (parasti šodiena)
- `watch_modified` – `true/false` vai checksum atšķiras pret iepriekšējo reizi
- `diary_days_by_profile` – cik dienas ielādētas katram profilam

---

## Debug logi

`configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.eklase_family: debug
    custom_components.eklase_family.coordinator: debug
    custom_components.eklase_family.api: debug

Atruna

Šī ir neoficiāla integrācija un nav saistīta ar E-klase izstrādātājiem.
E-klase var mainīt API/autentifikāciju, kas var ietekmēt integrācijas darbību.