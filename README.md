# TabloAI — suhbat orqali biznes tahlili

Asosiy interfeys 1C Enterprise paneli va AI tahlilchini birlashtiradi.
`start.bat` bilan ishga tushirgach, `http://localhost:5173` manzilini oching va
**AI Tahlilchi** bo'limiga o'ting. FastAPI shu interfeysni `/enterprise` orqali ham beradi.

Chat `POST /api/query/chat` orqali faol bazaga savol beradi. Javobda izoh,
bajarilgan SQL va natija qatorlarini ko'rish mumkin. Yetarli raqamli qatorlar bo'lsa,
chat ustunli, gorizontal ustunli, halqali, chiziqli yoki maydonli diagrammadan
bittasini tanlaydi. Vaqt qatorlari sana bo'yicha, reytinglar savolda so'ralgan
yo'nalishda saralanadi. Bitta sonli javob matnda ko'rsatiladi.

1C namunasidagi beshta tayyor savol mahalliy SQL bilan ishlaydi.
Erkin shakldagi savollar uchun OpenAI ishlatishda `backend/.env` faylida
`LLM_PROVIDER=openai` va `OPENAI_API_KEY` sozlanadi. 1C va Atlas Auto
namuna bazalaridagi ma'lumotlar sun'iy. Quyidagi Jarvis buyruqlari va ovozli
suhbat eski React ish maydoniga tegishli (`/index.react-legacy.html`, Vite orqali).

## Ulangan baza bilan ishlash

PostgreSQL yoki SQLite bazasi ulanganda tizim uning jadvallari, ustunlari,
kalitlari va bog'lanishlarini tekshiradi. Chatda `Baza o'rganildi` holati,
jadval va ustunlar soni, tuzilma tafsilotlari va tavsiya savollar ko'rinadi.
Profil to'liq bo'lmasa, ogohlantirish chiqadi va tahlil tayyor deb belgilanmaydi.
Bu jarayon yangi modelni o'qitish emas: mavjud AI baza tuzilmasiga tayangan holda
faqat o'qish so'rovlarini bajaradi.

Ulangan manbada chatbot quyidagilarni bajaradi:

- savolga SQL hisob-kitobi, izoh, jadval va mos grafiklar bilan javob beradi;
- umumiy dashboard yoki so'ralgan davr/kesim bo'yicha dashboard tayyorlaydi;
- tahlilni alohida saqlangan hisobotga aylantiradi, CSV/HTML yuklab beradi;
- ma'lumot sifati, bo'sh qiymatlar va bog'lanishlarni cheklangan auditda tekshiradi;
- foydalanuvchi bergan arifmetik ifodalarni hisoblaydi.

Tashqi bazaga namuna bazasining sana yoki avtomobil filtrlari qo'llanmaydi.
Har bir natija o'z manbasini ko'rsatadi. Ulanish so'rov davomida almashsa,
eskirgan natija rad etiladi; saqlangan hisobot eksporti esa o'z natijasini saqlaydi.
Suhbat tarixi ulanish identifikatori bo'yicha ajratiladi. `Baza o'rganildi`
degan holat barcha biznes atamalari tasdiqlandi degani emas: noma'lum ko'rsatkich
ta'rifi yoki yetishmayotgan ma'lumot uchun chatbot aniqlashtirish so'rashi mumkin.

Profil xulosasi: `GET /api/database/understanding`. `POST /api/database/connect`
ham shu xulosani `understanding` maydonida qaytaradi. Namuna avtosavdo bazasi
alohida tanlanadigan sinov manbasi bo'lib qoladi.

## Suhbatda bajariladigan ishlar

| Topshiriq | Natija |
| --- | --- |
| Sotuv dashboardini yarat | Hisoblangan ko'rsatkichlar, vaqt dinamikasi va filial/modellar kesimi |
| Oktabr hisobotini tayyorla | Saqlangan hisobot va CSV/HTML yuklab olish |
| Omborni tekshir | Haqiqiy agent tekshiruvi, topilgan holatlar va tavsiyalar |
| Pul tushumi qancha? | Parametrli SQL bilan tekshirilgan hisob, izoh, jadval/grafik |
| 1250000 * 12 + 500000 ni hisobla | Chegaralangan arifmetik hisoblagichning aniq natijasi |
| Savol ber yoki tushuntirish so'ra | OpenAI orqali suhbat javobi |
| Baza ulash | PostgreSQL/SQLite manbasini shu suhbatdan tanlash |

`POST /api/platform/command` buyruqni rejalashtiradi va bir so'rovda ko'pi bilan
3 yuqori darajadagi amalni bajaradi. Har bir tahlil amali ichida agent kerakli
vositalarni ketma-ket tanlab ishlatadi. Javobdagi `steps` bajarilgan ishlarni, `artifacts` esa haqiqiy
natijalarni beradi. Jarvis va agentlar alohida sahifalar o'rniga suhbat ortida
ishlaydi. Doimiy avtomatik monitoring yoki vaqt bo'yicha rejalashtirish yo'q.

## Ko'p bosqichli tahlil agenti

OpenAI ulanganida tashqi baza savollari `data_agent.py` dagi vositali agentga
beriladi. U sxemani izlaydi, jadvallarni tekshiradi, SQL bajaradi va natijaga
qarab keyingi so'rovni tanlaydi. Bitta jadval yoki oldindan yozilgan metrikalar
ro'yxati bilan cheklanmaydi. So'rov xatosi keyingi bosqichda tuzatilishi mumkin;
bo'sh natija topilishi filtrni olib tashlashga sabab bo'lmaydi.

Masalan: **“2024-yil yanvar-mart sotuvlari nega kamaydi? Oylar, mahsulotlar va
mijozlar kesimida tekshir, xulosa va tavsiyalar bilan dashboard tayyorla.”**
Agent bir nechta hisobni bajarib, dalillarga tayangan javob va grafiklar beradi.
Bog'liqlik asosida sababni isbotlangan deb ko'rsatmasligi kerak.

Suhbatdagi ochiladigan tafsilotlar haqiqiy vosita amallari, so'rov natijalari,
SQL va cheklovlarni ko'rsatadi; modelning ichki mulohazalari ko'rsatilmaydi.
Yakuniy javob alohida dalil tekshiruvidan o'tadi: so'ralgan jami son o'rniga
faqat kesim qaytarilishi, hisobga mos bo'lmagan raqam va tasdiqlanmagan sabab
da'volari qayta ishlashga yuboriladi. Bu model asosidagi tekshiruv bo'lib,
xatosiz javob kafolati emas; foydalanilgan SQL va natijalar ko'rib chiqish uchun ochiq.
Hisobotlar barcha bajarilgan so'rov dalillari bilan **Excel, CSV, HTML va JSON**
shaklida yuklanadi. Excel har bir natijani alohida varaqqa joylaydi.

Bir tahlil 16 vosita tanlash bosqichi, 3 tagacha yakuniy dalil tekshiruvi,
8 SQL urinishi va 180 soniya bilan chegaralangan.
Har bir natijadan ko'pi bilan 500 qator saqlanadi; modelga 40 qatorlik ko'rinish
beriladi. Agent to'liq agregat uchun yangi hisob so'rashi mumkin. Limit tugasa,
qisman natija aniq belgilanadi. SQLite va PostgreSQL so'rovlari uchun 15 soniya
ijro chegarasi mavjud. Manba bazasini o'zgartirish va erkin tizim kodini bajarish
ushbu vositalarga kirmaydi.

Vositali suhbat [OpenAI Responses function calling](https://developers.openai.com/api/docs/guides/function-calling)
asosida ishlaydi. Model `OPENAI_MODEL` orqali tanlanadi; mavjud `.env` modeli
avtomatik almashtirilmaydi.

Agentni sun'iy savdo bazasida jonli tekshirish (API sarfi bor; yakunda namuna
ulanishi qayta tiklanadi):

```powershell
python backend/scripts/check_agent_investigation.py --live-ai --output .codex_run/agent-investigation-live.json
```

Mikrofon yozuvi OpenAI orqali matnga aylanadi; foydalanuvchi matnni ko'rib,
yuboradi. Javobni sun'iy ovozda o'qish ham mavjud. Mikrofon ruxsati brauzerda
beriladi. Telefoniya, MySQL ulagichi va CSV import qo'shilmagan; CSV eksport bor.

Biznes bazalari faqat o'qiladi. Hisobotlar va agent natijalari alohida lokal
bazada saqlanadi. Tashqi xizmatga xabar yuborish, to'lov qilish, manba bazasini
o'zgartirish kabi ulanmagan amallar bajarilgan deb ko'rsatilmaydi. Tashqi
manbadagi tahlil universal SQL tahlilchisiga yo'naltiriladi.

Suhbat interfeysi ko'k palitrada. Rejim tugmasi faqat quyosh/oy belgisidan iborat;
har bir to'liq sahifa yangilanishi kunduzgi rejimdan boshlanadi.

## Namuna ma'lumotlari

Asosiy ish maydoni `backend/data/enterprise_1c.db` bilan ochiladi.
Jadvallar va ma'lumotlar tavsifi [1C manbasi hujjatida](backend/data/README.md).
Atlas Auto APIlaridan birinchi marta foydalanilganda ular uchun
`backend/data/dealership.db` alohida yaratiladi.

- **60 059 yozuv, 12 jadval:** 8 filial, 96 xodim, 6 000 mijoz, 40 model,
  8 000 avtomobil, 20 000 murojaat, 5 000 sotuv, 16 800 to'lov, 920 xarajat,
  3 000 servis buyurtmasi, 184 oylik maqsad va 11 metadata yozuvi.
- Davr: **2025-01-01 — 2026-11-12**. Noyabr 2026 to'liq oy emas.
- Pul: butun sonli **UZS**. Narxlar bozor kotirovkalari emas.
- Deterministik generator: `backend/app/dealership_seed.py`, seed `20261112`.

Qo'lda yaratish va jadval sonlarini ko'rish:

```powershell
cd backend
python -c "from app.dealership_seed import dataset_summary, ensure_demo_database; print(dataset_summary(ensure_demo_database()))"
cd ..
```

Atlas Auto hisobotlari va agent natijalari alohida `backend/data/platform_state.sqlite3`
faylida saqlanadi; tahlil qilinadigan manbaga yozilmaydi.

## O'rnatish

Python 3.12+ va Node.js 18+ kerak. Loyiha ildizida:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r backend/requirements.txt
cd frontend
npm.cmd install
cd ..
```

Konfiguratsiya nusxasini faqat fayl mavjud bo'lmasa yarating:

```powershell
if (-not (Test-Path -LiteralPath 'backend/.env')) {
    Copy-Item -LiteralPath 'backend/.env.example' -Destination 'backend/.env'
}
```

Mavjud `.env` faylini almashtirmang. Boshqa ulanish va maxfiy qiymatlarni
saqlagan holda quyidagilarni sozlang:

```dotenv
WORKSPACE_MODE=demo
LLM_PROVIDER=openai
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=gpt-4.1-mini
OPENAI_TIMEOUT=90
```

Kalit faqat backendda saqlanadi. Kalit bo'lmasa dashboard, ombor, agentlar,
hisobot va cheklangan namuna suhbati ishlaydi. Haqiqiy OpenAI javobi kalit,
modelga kirish, tarmoq va API hisobiga bog'liq.

Mavjud `open_ai_api_key` yoki `OPEN_AI_API_KEY` nomlari ham qabul qilinadi.
Bo'sh bo'lmagan `OPENAI_API_KEY` ustuvor. `.env` o'zgartirilganda backendni
qayta ishga tushiring. Audio modellarini kerak bo'lsa `OPENAI_TRANSCRIBE_MODEL`
va `OPENAI_VOICE_MODEL` bilan almashtirish mumkin.

## Ishga tushirish

Windowsda `start.bat` backend va frontendni alohida oynalarda ishga tushiradi.
8015 va 5173 portlari bo'sh bo'lishi kerak. Yoki ikki terminaldan foydalaning:

```powershell
# Birinchi terminal, loyiha ildizida
.\venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8015
```

```powershell
# Ikkinchi terminal
cd frontend
npm.cmd run dev
```

- Interfeys: <http://localhost:5173>
- API hujjatlari: <http://localhost:8015/docs>
- Holat: `GET /api/health`

Ilova hozir **localhost uchun bir foydalanuvchili ish maydoni**. Login,
kompaniyalararo izolyatsiya va ko'p foydalanuvchili ruxsatlar qo'shilmagan.
Model tayyor bo'lmasa health `degraded` qaytarishi mumkin; bu namuna dashboardi
ishlamaydi degani emas.

## Namuna va tashqi manbalar

`/api/platform/*` — Atlas Auto namuna bazasi uchun dashboard, chat, agent va
hisobot APIlari. Tashqi baza ulanishi ushbu panelning manbasini almashtirmaydi.

Universal tahlilchi alohida ishlaydi: `POST /api/database/connect` orqali
PostgreSQL yoki mavjud SQLite fayli ulanadi, sxema qayta o'rganiladi va
`POST /api/query/chat` orqali savol beriladi. Interfeysdagi universal tahlil
shu yo'lni ishlatadi. Ulanish ma'lumotlari xotirada saqlanadi; endpoint `.env`
faylini qayta yozmaydi.

Tashqi manbani startupda ulash uchun `WORKSPACE_MODE=external` va `DB_HOST`,
`DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_INCLUDE_SCHEMAS` ni sozlang.
Bevosita ulanish uchun `DATABASE_URL` ham mavjud. SSH uchun `SSH_HOST`,
`SSH_USER`, `SSH_PKEY_PATH` ishlatiladi; private kalit loyiha tashqarisida
saqlanadi. Eski `ACTIVE_DB` va mijoz prefiksli parametrlar orqaga moslik uchun
saqlangan. `LLM_PROVIDER=vllm` eski model endpointini tanlaydi.

## Ko'rsatkichlarning ma'nosi

- **Sotuv:** faqat `completed` sotuvlar summasi; pul tushumi emas.
- **Yalpi foyda:** sotuv minus avtomobil tannarxi. Operatsion xarajatlar
  ayirilmagan; sof foyda deb talqin qilinmaydi.
- **Pul tushumi:** to'lov sanasi tanlangan davrga tushgan haqiqiy qayd etilgan
  to'lovlar. Tegishli sotuv oldingi davrda bo'lishi mumkin.
- **Qarzdorlik:** tanlangan davrdagi sotuvlardan davr oxirigacha tushgan
  to'lovlar ayiriladi. Bu barcha tarixiy yoki muddati o'tgan qarz emas.
- **Ombor:** 2026-11-12 holatidagi mavjud va band avtomobillar. Filial/tur
  filtri ishlaydi; sana filtri tarixiy omborni qayta tiklamaydi.
- **Konversiya:** tanlangan davrda yaratilgan murojaatlarning hozirgi `won` ulushi.
- **Taqqoslash:** oldingi teng uzunlikdagi kalendar davr. Ma'lumot yetarli
  bo'lmasa o'zgarish foizi ko'rsatilmaydi.

## Tekshirish

Loyiha ildizida, `pytest` o'rnatilgan Python muhitida:

```powershell
.\venv\Scripts\python.exe -m pytest backend/tests -q
cd frontend
npm.cmd run build
node scripts/check-connected-workflow.mjs
```

Generator testi qo'shimcha kutubxona talab qilmaydi:

```powershell
.\venv\Scripts\python.exe -m unittest backend.tests.test_demo_seed -v
```

Yangi suhbat buyruqlarini haqiqiy OpenAI bilan tekshirish (API sarfi bor;
namuna hisobot va agent natijalarini yaratadi):

```powershell
python backend/scripts/check_assistant.py --live-ai --output .codex_run/assistant-live.json
```

Bu tekshiruv hisoblagich, dashboard, saqlangan hisobot/eksport, agent,
pul tushumi, oddiy suhbat, bir nechta amal, oldingi davrni saqlaydigan davomiy
savol va bajarilmaydigan buyruqni qamraydi.

Server ishlayotganda API orqali tekshirish:

```powershell
python backend/scripts/acceptance_check.py --output .codex_run/acceptance.json
```

Haqiqiy OpenAI tahlilini uchta namuna savoli bilan tekshirish (API sarfi bor):

```powershell
python backend/scripts/acceptance_check.py --live-ai --output .codex_run/acceptance-live.json
```

Skript dashboard yig'indilari, ombor qidiruvi/sahifalari, saqlangan hisobotlar
va xato javoblarini tekshiradi. Jonli rejimda 2025-yil sotuv soni, Cobalt
sotuvi va pul tushumini mustaqil reference SQL bilan solishtiradi. Tashqi
baza ulanishini almashtirmaydi yoki biznes yozuvlarini o'zgartirmaydi.
Bu tekshiruvlar brauzer maketi, jismoniy mikrofon, haqiqiy PostgreSQL serveri
va barcha mumkin bo'lgan savollar uchun kafolat emas.

Universal tahlilchini alohida sun'iy SQLite bazada tekshirish (bitta pullik
AI so'rovi):

```powershell
python backend/scripts/check_universal_sqlite.py
# Shu tahlilni yangi chatbot orqali tekshirish:
python backend/scripts/check_universal_sqlite.py --assistant
```

Bu skript faqat joriy manba namuna baza bo'lsa ishlaydi. `.codex_run` ichida
5 yozuvli yangi test bazasi yaratadi, uni ulaydi, haqiqiy `/api/query/chat`
javobini reference SQL bilan solishtiradi va namuna ulanishini qayta tiklaydi.
Natija `.codex_run/universal-sqlite-acceptance.json` da saqlanadi. Skriptni
boshqa foydalanuvchi shu lokal ish maydonida ishlamayotganda bajaring.

Ulangan baza bilan to'liq chatbot oqimini alohida 2024-yil chakana savdo
bazasida tekshirish (haqiqiy OpenAI chaqiruvlari):

```powershell
python backend/scripts/check_connected_workspace.py --live-ai --output .codex_run/connected-workspace-live.json
```

Skript bazani o'rganish, savolga aniq hisob, davrli va umumiy dashboard,
saqlangan hisobotning CSV/HTML eksporti hamda hisoblagichni tekshiradi.
Hisoblar mustaqil SQL bilan solishtiriladi. Faqat namuna manba faol bo'lganda
boshlanadi va yakunda uni tiklaydi; manbani almashtiradigan boshqa ish bilan
bir vaqtda bajarmang. Hisobot eksporti manba almashgandan keyin ham tekshiriladi.

OpenAI ovoz yaratish va transkripsiya xizmatlarini birgalikda tekshirish
(API sarfi bor):

```powershell
python backend/scripts/check_voice.py --output .codex_run/voice-live.json
```

Bu yozilgan sun'iy audioning server orqali aylanishini tekshiradi; jismoniy
mikrofon va brauzer ruxsatini sinamaydi. Jarvis mikrofonini brauzerda alohida
yoqib, qisqa savol bilan tekshiring.

`POST /api/evaluation/gold/default` o‘rnatilgan 1C demo bazasida 20 ta savdo
savolini chat orqali tekshiradi. Etalon hisoblar va kutilgan aniqlashtirishlar
[`enterprise_sales_cases.json`](backend/enterprise_sales_cases.json) faylida.
Reytinglarda filiallar tartibi, summalar va diagrammadagi qiymatlar ham tekshiriladi.
Erkin savollar faol model kvotasini sarflaydi. Model limitga yetsa, qolgan
savollar `not_run` holatini oladi.

Korxona bazasi uchun tahlilchi tasdiqlagan savollar va SQL hisoblarini
`POST /api/evaluation/gold` ga yuboring. Natijadagi `wrong_answer`,
`clarification`, `error` va `not_run` holatlari alohida qaytariladi.
Kutilgan natija ma’lumot yetishmasligi bo‘lsa, savolga
`"expected_error": "metric_unavailable"` yoki
`"expected_error": "clarification_required"` bering. [Tekshiruv tafsilotlari](SECURITY_AND_VALIDATION.md).
