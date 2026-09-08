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
- 👀 “Watch” režīms: salīdzina katra skolēna pirmo stundu šodienai/rītdienai (vai N dienām) un ziņo, ja diena sākas agrāk vai vēlāk.

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

### Atšķirīgi stundu laiki katram skolēnam

1. Pēc integrācijas ielādes atver tās **Configure / Konfigurēt** iestatījumus.
2. Izvēlies **Skolēna stundu laiki**, tad vajadzīgo skolēnu.
3. Norādi šī skolēna stundu sākuma un beigu laikus formātā **HH:MM** un saglabā.
4. Atkārto otram skolēnam, ja arī viņam nepieciešami individuāli laiki.

Katrs grafiks tiek piesaistīts E-klases profila ID. Kalendārs paliek kopīgs, bet
katra skolēna notikumiem tiek izmantoti viņa stundu laiki. Esošie iestatījumi
joprojām darbojas: skolēni bez individuāla grafika izmanto kopīgos stundu laikus.

Lai atgrieztos pie kopīgā grafika, skolēna laiku formā atzīmē
**Izmantot kopīgos stundu laikus** un saglabā. Kopīgo grafiku un konta
iestatījumus var mainīt sadaļā **Konts un kopīgie stundu laiki**.

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
- `watch_modified` – `true`, ja pēdējā veiksmīgajā datu ielādē konstatēta pirmās stundas sākuma maiņa
- `watch_revision` – palielinās katrā ielādē, kurā konstatētas šādas izmaiņas
- `watch_changes` – pēdējā ielādē konstatēto izmaiņu saraksts ar skolēnu, datumu, veco/jauno stundas numuru un sākuma laiku
- `diary_days_by_profile` – cik dienas ielādētas katram profilam

---

## Brīdinājumi par pirmo stundu

Ar `watch_days: 2` tiek pārbaudīta **šodiena un rītdiena**. Aiznākamās dienas
izmaiņas paziņojumu nerada. Tālāko ielādēto dienu sākuma stundas tiek saglabātas,
lai tās būtu pieejamas salīdzināšanai, kad šie datumi nonāk novērošanas periodā.

Tiek salīdzināta pirmā reālā stunda pēc `lessonNumber`, arī tad, ja atceltā stunda
vienkārši pazūd no atbildes. Sākuma laiku iegūst no skolēna individuālā vai kopīgā
grafika. Kabinetu, priekšmeta, mājasdarbu un vēlāko stundu izmaiņas šo brīdinājumu
nerada. Izmaiņas tikai konfigurētajos zvanu laikos arī nerada brīdinājumu.

Pirmā ielāde pēc pārejas no vecās `timeModified` loģikas izveido sākuma datus bez
brīdinājuma. Tie saglabājas pāri restartiem. Jauns datums pats par sevi nav izmaiņa.
Tukša vai trūkstoša diena saglabā iepriekšējo sākuma stundu un netiek uzskatīta par
atcelšanu — tādēļ šis mehānisms nepaziņo arī par visas mācību dienas atcelšanu.

**Ieteicamais automatizācijas trigeris** ir notikums, nevis `watch_modified`
pāreja uz `true`: notikums rodas arī divās secīgās ielādēs ar izmaiņām.
Esošajā automatizācijā nomaini trigeri uz:

```yaml
trigger: event
event_type: eklase_family_first_lesson_changed
```

Paziņojuma darbības `message` laukā var izmantot:

```yaml
message: "{{ trigger.event.data.message }}"
```

Piemērs: `Anna (2026-09-09): pirmā stunda tagad 09:20, iepriekš 08:30.`
Notikuma datos ir arī `entry_id` un `changes` saraksts. Ja ir vairāki E-klases
integrācijas ieraksti, trigerim var pievienot `event_data` filtru ar attiecīgo
`entry_id`. Integrācija pati telefona paziņojumus nesūta.

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