# Manba xavfsizligi va tahlil tekshiruvlari

Ilova localhostda bir foydalanuvchi uchun ishlaydi. Autentifikatsiya, alohida
tenantlar va foydalanuvchiga xos row-level ruxsatlar hali joriy qilinmagan.

## Manbalar va yozish chegarasi

- `backend/data/dealership.db` generator tomonidan fayl yo'q bo'lsa yaratiladi;
  mavjud faylni almashtirish faqat `--force` bilan.
- Namuna APIlari SQLite `mode=ro` va `PRAGMA query_only=ON` ishlatadi.
  Filtr qiymatlari parametrli so'rovlarga uzatiladi.
- Hisobotlar va agent natijalari alohida `platform_state.sqlite3` ga yoziladi.
- Tashqi PostgreSQL/SQLite ulanishi universal tahlilchiga tegishli. Atlas Auto
  paneli tashqi bazadan ma'lumot olayotgandek ko'rsatilmasligi kerak.
- Agentlar foydalanuvchi ishga tushiradigan hisob-kitob va AI xulosasidan
  iborat. AI xatosi hisoblangan natijani yo'qotmaydi; xulosa olinmagani
  alohida ko'rsatiladi. Fon jadvali, tashqi xabar yuborish va manba bazaga
  yozish mavjud emas.

## Tashqi bazadagi himoya qatlamlari

1. PostgreSQL `default_transaction_read_only=on` va 15 soniyalik statement
   timeout bilan ishlaydi.
2. SQLite `PRAGMA query_only=ON` ishlatadi va mavjud bo'lmagan tashqi faylni
   yaratmaydi. SQL progress handler 15 soniyadan oshgan so'rovni uzadi va
   ulanish poolga qaytishidan oldin tozalanadi.
3. SQLAlchemy cursor chegarasida yozuvchi va DDL buyruqlari rad etiladi.
4. `DatabaseManager.execute_query` faqat bitta AST orqali tekshirilgan `SELECT`
   yoki `WITH ... SELECT` so'rovini bajaradi.
5. Chat SQL faqat profillangan jadvallar va tekshirilgan ustunlarga murojaat qiladi.
6. E'lon qilingan foreign keylar hisobga olinadi. Taxmin qilingan joinlar
   read-only uniqueness tekshiruvidan o'tadi; xavfli cardinality va parent-side
   summalarning ko'payishi rad etiladi.
7. SQL savol shakli bilan solishtiriladi: agregat talabi, top-N ORDER BY/LIMIT,
   aniq davr uchun sana filtri va bir nechta manbali taqqoslashlar tekshiriladi.
8. Model/tarmoq xatosi bo'sh natija sifatida berilmaydi; alohida xato qaytariladi.
9. Chat tashqi manbaning kutilgan fingerprintini yuboradi. Faol ulanish
   mos kelmasa, rejalashtirish boshlanmaydi. Buyruq davomida ulanish yoki
   profillangan tuzilma almashsa, eskirgan natija rad etiladi.
10. To'liq bo'lmagan profil bilan tashqi tahlil, dashboard va hisobot
    tayyorlanmaydi. Profil xulosasi sample row qiymatlarini yubormaydi.
11. Tashqi hisobot eksporti saqlangan natijadan olinadi; joriy ulangan
    bazadan qayta hisoblanmaydi. HTML qiymatlari escape qilinadi, CSV matn
    kataklaridagi formula prefikslari zararsizlantiriladi. Eksportda manba,
    so'rov va natija cheklanishi ko'rsatiladi.

Tashqi bazada faqat `SELECT` huquqli hisobdan foydalaning. SSH private kalitni
repository tashqarisida saqlab, to'liq yo'lini `SSH_PKEY_PATH` da ko'rsating.

## Model va konfiguratsiya

`OPENAI_API_KEY` backend `.env` faylida saqlanadi; frontendga berilmaydi.
`.env.example` faqat `.env` yo'q bo'lsa nusxalanadi. Mavjud secretlar namuna
konfiguratsiyasi bilan almashtirilmaydi.
`open_ai_api_key` va `OPEN_AI_API_KEY` aliaslari ham qabul qilinadi.

Namuna bazasining tezkor ko'rsatkichlari parametrli SQL hisoblaridan olinadi.
Ulangan manba uchun erkin tahlil savollari vositali agentga beriladi. U sxema
qidiruvi, jadval tekshiruvi va SQL hisobini ketma-ket tanlaydi. Har bir SQL
read-only AST, sxema, bog'lanish va tegishli biznes qoidalaridan o'tadi.
So'rovlar, jadval natijalari va grafiklar bajarilgan dalil identifikatorlariga
bog'lanadi; model grafik qatorlarini o'zi yubormaydi. AIga natijaning cheklangan
ko'rinishi yuboriladi; to'liq ko'rinish yo'qligi modelga ochiq bildiriladi.
Schema va baza qiymatlari ishonchli ko'rsatma sifatida olinmaydi. Modelning
ichki mulohazalari faqat Responses davomiyligi uchun qayta uzatiladi, UIga
chiqarilmaydi. Tashqi manbani ulashda AIga uzatiladigan ma'lumot uchun
manbaning ruxsatlarini hisobga olish kerak.

Agent bir tahlilda 16 model bosqichi, 20 vosita chaqiruvi, 8 SQL urinishi va
180 soniya bilan chegaralangan. Yakuniy xulosa 3 tagacha alohida model
tekshiruvidan o'tishi mumkin; tekshiruv bajarilmasa natija qisman belgilanadi.
Bu semantik tekshiruv matematik yoki sababiy to'g'rilikning formal isboti emas.
Har so'rovdan 500 qatorgacha saqlanadi,
modelga 40 qatorgacha beriladi. Limit yoki model uzilishi qisman natija
sifatida belgilanadi. Saqlangan hisobotning Excel/CSV/HTML/JSON eksportlari
bir xil bajarilgan dalillardan olinadi; Excel formulalari bajariladigan
ko'rinishga aylantirilmaydi.

Kalitsiz suhbatning cheklangan namuna rejimi aniq ko'rsatiladi; bu OpenAI
javobi emas. Jarvis mikrofon ruxsatidan so'ng yozilgan audioni backend orqali
OpenAIga yuboradi. Audio yuklash 10 MB bilan, ovozga aylantirish matni 2 000
belgi bilan chegaralangan. Server kalitni brauzerga bermaydi va audio javobiga
`Cache-Control: no-store` qo'yadi. Ovoz uchun modelga kirish va tarmoq kerak;
brauzer nutq imkoniyati zaxira yo'li bo'lishi mumkin.

## Namuna hisoblarining tekshiruvi

- Pul butun sonli UZS. Generator foreign key, unique VIN va takrorlanmagan
  sotuv avtomobili cheklovlarini yaratadi.
- Yakunlangan sotuv avtomobili `sold`, filiali va tannarxi mos.
- Bekor qilingan sotuvda to'lov yo'q. To'lovlar sotuv summasidan oshmaydi;
  sanasi sotuvdan oldin yoki 2026-11-12 dan keyin emas.
- Sotuv metrikalari sotuv darajasida hisoblanadi. To'lovlar avval sale ID
  bo'yicha yig'ilib ulanadi; join summalarni ko'paytirmaydi.
- Yalpi foydadan operatsion xarajatlar ayirilmagan. Qarzdorlik tanlangan
  sotuv davri va to'lov cutoff sanasiga tegishli. Ombor 2026-11-12 snapshotidir.
- Oldingi davr teng uzunlikda olinadi; yetishmagan davr nol deb talqin
  qilinib foizga aylantirilmaydi.
- Noyabr 2026 qisman mavjud; oylik maqsad esa to'liq oy uchun. Statistik farq
  o'z-o'zidan sababni isbotlamaydi.

`backend/tests/test_demo_seed.py` hajm, FK/integrity, moliyaviy bog'lanishlar,
idempotentlik va deterministik qayta yaratishni tekshiradi. Platforma testlari
filtrlar, eksport va agent/hisobot natijalarini tekshiradi. OpenAI unit-testlari
transportni almashtirib ishlaydi; ularning o'tishi haqiqiy kalit yoki modelga
kirish ishlayotganini isbotlamaydi.

`test_end_to_end.py` haqiqiy FastAPI appni vaqtinchalik holat/kesh va alohida
DatabaseManager bilan ochadi. U startup, tashqi SQLite profilini almashtirish,
yozishni rad etish, muvaffaqiyatsiz ulanishda oldingi manbani saqlash, eksport
va OpenAI rejasidan haqiqiy SQL natijasigacha bo'lgan oqimni tekshiradi.
Model transporti almashtiriladi; testlar pullik so'rov yubormaydi. Eski
schema-routing regressiyalari foydalanuvchining metadata keshiga bog'liq emas.

`backend/scripts/acceptance_check.py --live-ai` alohida ruxsat etilgan jonli
sinov: uchta sun'iy biznes savolining hisobini mustaqil SQL bilan solishtiradi.
Kalit, provider javob tanasi yoki mijoz yozuvlari konsolga chiqarilmaydi.

`check_universal_sqlite.py` joriy manba demo ekanini tekshirgach, faqat yangi
5 yozuvli sun'iy bazaga vaqtincha ulanadi. Savol 120 soniya bilan cheklangan;
`finally` bo'limi demo ulanishini qayta tiklashga urinadi va natijani qayd etadi.
Bu lokal manba almashishi sababli skript boshqa ishlar bilan bir vaqtda
bajarilmasligi kerak. `check_voice.py` OpenAI audio yaratish/transkripsiya
aylanishini sun'iy matn bilan tekshiradi; mikrofon ruxsatini chetlab o'tmaydi.

## Mavjud tekshirish endpointlari

| Endpoint | Tekshiruv |
| --- | --- |
| `POST /api/data-quality/audit` | Chegaralangan NULL profili, kalit dublikatlari, so'nggi timestamp, tanlama orphan-key va relationship holati |
| `POST /api/evaluation/gold` | 50 tagacha tasdiqlangan savol/reference-SQL; qiymat, ustun, qator soni va dublikat ko'pligini solishtirish |
| `POST /api/evaluation/gold/default` | 1C demo bazasida `backend/enterprise_sales_cases.json` dan 20 ta savolni tekshiradi. Boshqa manbada `backend/gold_cases.json` uchun baza nomi va sxema mosligi tekshiriladi. |

Gold savollari ulangan bazaga mos bo'lishi kerak. Boshqa manba ulanganida
eski savollarni mos reference SQLsiz ishlatmang. Reference SQL ham faqat
o'qish tekshiruvidan o'tadi.

Audit misoli; jadval nomlarini ulangan manbaga moslang:

```json
{
  "tables": ["sales", "customers"],
  "max_tables": 10,
  "sample_rows": 5000,
  "include_relationships": true
}
```

Sxema o'zgarganda metadata profilini yangilang. Profiler primary/unique key,
foreign key, nomzod kalitlar, sxema fingerprinti va table/column driftni qayd
etadi. Noma'lum bazalar jadval/ustun nomlari, turlar, kalitlar va umumiy biznes
tushunchalari asosida o'rganiladi; noaniq savol aniqlashtirish talab qilishi mumkin.

Test va frontend build buyruqlari [README.md](README.md#tekshirish) da berilgan.
