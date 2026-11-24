# 2026-yil oktabr savdosi va pul tushumi

TabloAI · Sun’iy 1C namuna bazasi · Sentabr bilan taqqoslash · 2026-yil 24-noyabr

## Qisqa xulosa

Oktabrda 187 ta yakunlangan buyurtma hisobiga 48,69 mlrd so‘mlik sotuv qayd etildi. Sentabrga nisbatan buyurtmalar soni 29 taga, sotuv 5,02% ga kamaydi. Yalpi foyda 2,96% ga pasaydi, yalpi marja esa 0,41 foiz bandiga oshdi. [1](#oylik)

Oktabr davomida kelib tushgan pul 49,30 mlrd so‘mni tashkil etdi. Bu summa boshqa oylardagi buyurtmalarga shu oyda tushgan to‘lovlarni ham qamrab olishi mumkin. Toshkent Bosh Ofis oktabr sotuvining 77,47% ini berdi. [1](#oylik) · [2](#filiallar)

<a id="oylik"></a>
## 1. Oylik natijalar

| Ko‘rsatkich | Sentabr | Oktabr | Mutlaq o‘zgarish | Nisbiy o‘zgarish |
|---|---:|---:|---:|---:|
| Yakunlangan buyurtmalar, ta | 216 | 187 | −29 | -13,43% |
| Sotuv, so‘m | 51 265 637 500 | 48 693 322 500 | −2 572 315 000 | -5,02% |
| Sotilgan mahsulot tannarxi, so‘m | 41 687 700 000 | 39 398 700 000 | −2 289 000 000 | -5,49% |
| Yalpi foyda, so‘m | 9 577 937 500 | 9 294 622 500 | −283 315 000 | -2,96% |
| Qabul qilingan to‘lovlar, ta | 221 | 194 | −27 | -12,22% |
| Pul tushumi, so‘m | 53 061 390 000 | 49 295 380 000 | −3 766 010 000 | -7,10% |
| Yalpi marja | 18,68% | 19,09% | +0,41 foiz bandi | — |

Hisobga ikki to‘liq kalendar oy kiritildi. Sentabrda 30, oktabrda 31 kun bor. Nisbiy o‘zgarish sentabr summasiga nisbatan hisoblandi. Yalpi marja — yalpi foydaning sotuv summasiga nisbati.

<a id="filiallar"></a>
## 2. Oktabrda filiallar bo‘yicha sotuv

| Filial | Yakunlangan buyurtmalar, ta | Sotuv, so‘m | Jami sotuvdagi ulush |
|---|---:|---:|---:|
| Toshkent Bosh Ofis | 146 | 37 724 922 500 | 77,47% |
| Navoiy Sanoat-IT Filiali | 15 | 3 923 300 000 | 8,06% |
| Samarqand IT Filiali | 17 | 3 902 850 000 | 8,02% |
| Fargona IT Filiali | 9 | 3 142 250 000 | 6,45% |
| Buxoro IT Filiali | 0 | 0 | 0,00% |
| Andijon IT Filiali | 0 | 0 | 0,00% |
| **Jami** | **187** | **48 693 322 500** | **100%** |

Navoiy va Samarqand orasidagi farq 20 450 000 so‘m. Buxoro va Andijonda oktabrda yakunlangan buyurtmalar soni nol. Filiallar buyurtma sanasi va sotuv qiymati bo‘yicha taqqoslandi.

## 3. Hisoblash chegaralari

- **Sotuv** uchun `orders.status = 'completed'` shartiga mos buyurtmalarning `total_amount` qiymatlari buyurtma sanasi bo‘yicha jamlandi. Bazada alohida yakunlash sanasi ko‘rsatilmagan. Boshqa hisobotlarda bekor qilinmagan barcha buyurtmalar olinadigan bo‘lsa, savdo summasi farq qiladi.
- **Tannarx** uchun shu buyurtmalarning `total_cost` qiymatlari olindi. **Yalpi foyda** sotuvdan shu tannarxni ayirish bilan hisoblandi. Operatsion xarajatlar va soliqdan keyingi foyda alohida hisob talab qiladi.
- **Pul tushumi** uchun `payments.amount` qiymatlari `payment_date` bo‘yicha jamlandi. Bank, kassa va korporativ karta orqali tushgan barcha to‘lovlar qamrab olindi. Sotuvga ham, pul tushumiga ham mustaqil sana filtri qo‘llandi.
- **Manba** — [loyihaga biriktirilgan SQLite bazasi](../backend/data/enterprise_1c.db). Hisobot shu sun’iy bazadagi chat javoblarini tekshirish uchun tayyorlandi. Real korxonaning moliyaviy hisoboti bilan solishtirish uchun uning tasdiqlangan raqamlari kerak.

## 4. Tekshiruv va qayta hisoblash

Baza faqat o‘qish rejimida ochilib, barcha hisob bitta tranzaksiyada bajarildi. Oylik summalar va filiallar tartibi avval quyidagi SQL bilan olindi. Keyin Python’da buyurtmalar va to‘lovlarning alohida yozuvlari sanasi bo‘yicha guruhlanib, barcha summalar va sonlar mustaqil qayta hisoblandi. 403 ta buyurtmaning har birida `order_items.total_amount` yig‘indisi buyurtma summasiga, `quantity × cost_price` yig‘indisi buyurtma tannarxiga teng chiqdi. Oltita filialning oktabr summasi va buyurtmalar soni oylik jami bilan mos keldi.

**Oylik hisob uchun SQL**

```sql
WITH sales AS (
  SELECT substr(order_date, 1, 7) AS month, COUNT(*) AS order_count,
         SUM(total_amount) AS sales_uzs, SUM(total_cost) AS cost_uzs
  FROM orders
  WHERE status = 'completed'
    AND order_date >= '2026-09-01' AND order_date < '2026-11-01'
  GROUP BY substr(order_date, 1, 7)
), receipts AS (
  SELECT substr(payment_date, 1, 7) AS month, COUNT(*) AS payment_count,
         SUM(amount) AS receipts_uzs
  FROM payments
  WHERE payment_date >= '2026-09-01' AND payment_date < '2026-11-01'
  GROUP BY substr(payment_date, 1, 7)
)
SELECT s.month, s.order_count, s.sales_uzs, s.cost_uzs,
       s.sales_uzs - s.cost_uzs AS gross_profit_uzs,
       r.payment_count, r.receipts_uzs
FROM sales s JOIN receipts r ON r.month = s.month
ORDER BY s.month;
```

**Filiallar uchun SQL**

```sql
SELECT b.name AS branch, COUNT(o.id) AS order_count,
       COALESCE(SUM(o.total_amount), 0) AS sales_uzs
FROM branches b
LEFT JOIN orders o ON o.branch_id = b.id AND o.status = 'completed'
     AND o.order_date >= '2026-10-01' AND o.order_date < '2026-11-01'
GROUP BY b.id, b.name
ORDER BY sales_uzs DESC, b.id;
```
