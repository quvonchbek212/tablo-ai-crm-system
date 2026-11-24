import { CHART_COLORS } from '../theme';
import React, { useState, useMemo } from 'react';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  AreaChart,
  Area,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend
} from 'recharts';
import {
  Table as TableIcon,
  BarChart3,
  TrendingUp,
  PieChart as PieIcon,
  Download,
  Search,
  ChevronLeft,
  ChevronRight,
  RotateCcw,
  X,
  CheckCircle2,
  ArrowUpDown,
  Activity,
  SlidersHorizontal,
  LayoutGrid,
  Award,
  Layers,
  Sparkles,
  AlertTriangle
} from 'lucide-react';

const MULTI_COLORS = CHART_COLORS;

// Enterprise Uzbek dictionary for raw database enums, codes, and values
export const UZBEK_VALUE_DICTIONARY = {
  // Egalik shakllari (Ownership types)
  'RENTED': 'Ijaraga olingan',
  'OWNED': "Xususiy (O'ziniki)",
  'LEASED': 'Lizing asosida',

  // Toifalar (Vehicle categories)
  'LIGHT': 'Yengil',
  'SPECIAL': 'Maxsus texnika',
  'HEAVY': "Og'ir yuk",
  'MEDIUM': "O'rta yuk",

  // Transport turlari (Transport types)
  'DUMP_TRUCK': 'Samosval',
  'SPECIALIZED': 'Maxsus transport',
  'MANIPULATOR': 'Manipulyator',
  'WATER_TRUCK': 'Suv tashuvchi mashina',
  'MOBILE_CRANE': 'Avtokran',
  'TRACTOR': 'Traktor',
  'EXCAVATOR': 'Ekskavator',
  'BUS': 'Avtobus',
  'FRONT_LOADER': 'Frontal yuklagich',
  'CONCRETE_MIXER': 'Beton qorgich (Mikser)',
  'AERIAL_PLATFORM': 'Avtovishka',
  'BULLDOZER': 'Buldozer',
  'FLATBED': 'Bortli yuk mashinasi',
  'VAN': 'Furgon',
  'LONG_LOAD_TRUCK': 'Uzun yuk mashinasi (Dlinnomer)',
  'CONCRETE_PUMP': 'Avtobeton nasos',
  'PASSENGER_CAR': 'Yengil avtomobil',

  // Statuslar va holatlar (Statuses)
  'DRAFT': 'Qoralama',
  'PENDING_TRANSPORT_APPROVAL': "Transport tasdig'ini kutmoqda",
  'PENDING_DEPARTMENT_APPROVAL': "Bo'lim tasdig'ini kutmoqda",
  'AWAITING_CUSTOMER_CONFIRMATION': "Buyurtmachi tasdig'ini kutmoqda",
  'READY_FOR_PLANNING': 'Rejaga tayyor',
  'ASSIGNED_TO_BRANCH': 'Filialga biriktirilgan',
  'APPROVED': 'Tasdiqlangan',
  'CANCELLED': 'Bekor qilingan',
  'IN_PROGRESS': 'Jarayonda',
  'COMPLETED': 'Bajarilgan',
  'ASSIGNED': 'Biriktirilgan',
  'SUBMITTED': 'Yuborilgan',
  'CLOSED': 'Yopilgan',
  'IN_USE': 'Amalda (Ishlatilmoqda)',
  'ISSUED': 'Berilgan / Chiqarilgan',
  'RETURNED': 'Qaytarilgan',
  'STARTED': 'Boshlangan',
  'UNLOADED': 'Tushirilgan',
  'ACTIVE': 'Faol (Ishda)',
  'INACTIVE': 'Nofaol',
  'AVAILABLE': "Bo'sh / Mavjud",
  'BLOCKED': 'Bloklangan',
  'FIT': 'Soz / Yaroqli',
  'UNFIT': 'Nosoz / Yaroqsiz',
  'PASSED': "O'tgan",
  'FAILED': "O'tmagan",
  'MAINTENANCE': "Ta'mirlashda",
  'REPAIR': "Ta'mirda",
  'NEW': 'Yangi',
  'PROCESSING': "Ko'rib chiqilmoqda",
  'WAITING': 'Kutilmoqda',
  'SUCCESS': 'Muvaffaqiyatli',
  'REJECTED': 'Rad etilgan',
  'PAUSED': "To'xtatilgan",
  'CONFIRMED': 'Tasdiqlangan',
  'DELIVERED': 'Yetkazilgan',

  // Muhimlik darajalari (Priority)
  'CRITICAL': "O'ta muhim (Kritik)",
  'URGENT': 'Shoshilinch',
  'HIGH': 'Yuqori',
  'NORMAL': 'Oddiy',
  'LOW': 'Past',

  // Ehtiyot qismlar va agregatlar (Parts)
  'BATTERY': 'Akkumulyator',
  'TIRE': 'Shina (Balon)',
  'INSTALLED': "O'rnatilgan",
  'WORN': 'Eskirgan',
  'SPARE_PART': 'Ehtiyot qism',
  'OIL_FILTER': 'Moy filtri',
  'BRAKE_PAD': 'Tormoz kolodkasi',
  'IN_STOCK': 'Omborda mavjud',
  'REPLACED': 'Almashtirilgan',
  'SCRAPPED': 'Hisobdan chiqarilgan',
  'GOOD': 'Yaxshi',
  'BAD': 'Yomon',

  // Yo'l varaqa turlari (Waybill types)
  'PAPER_WAYBILL': "Qog'oz yo'l varaqasi",
  'ELECTRONIC_WAYBILL': "Elektron yo'l varaqasi",
  'CRANE': 'Avtokran',
  'LONG_HAUL': 'Shaharlararo',
  'LOCAL': 'Mahalliy (Shahar ichi)',
  'MOTOHOURS_ONLY': 'Faqat moto-soat',
  'KM_ONLY': 'Faqat kilometr',
  'KM_AND_MOTOHOURS': 'Km va moto-soat',

  // Ish rejimlari (Work modes)
  'SMENA': 'Smena',
  'XIZMAT SAFARDA': 'Xizmat safarida',
  'MAHALLIY': 'Mahalliy',

  // Ko'rik turlari (Inspection types)
  'PRE_TRIP': 'Reys oldi',
  'POST_TRIP': 'Reysdan keyingi',
  'PERIODIC': 'Davriy',

  // Yoqilg'i turlari (Fuel)
  'DIESEL': 'Dizel',
  'DIZEL': 'Dizel',
  'PETROL': 'Benzin',
  'BENZIN': 'Benzin',
  'CNG': 'Metan',
  'METAN': 'Metan',
  'PROPAN': 'Propan',

  // Boolean
  'TRUE': 'Ha / Ruxsat berilgan',
  'FALSE': "Yo'q / Ruxsat berilmagan"
};

// Automatic converter for any database value to human-readable Uzbek
export const humanizeUzbekValue = (val) => {
  if (val === null || val === undefined) return '';
  if (typeof val === 'boolean') {
    return val ? 'Ha / Ruxsat berilgan' : "Yo'q / Ruxsat berilmagan";
  }
  if (typeof val === 'number') return val;
  const str = String(val).trim();
  if (!str) return '';

  const upper = str.toUpperCase();
  if (UZBEK_VALUE_DICTIONARY[upper]) {
    return UZBEK_VALUE_DICTIONARY[upper];
  }

  const normalized = upper.replace(/\s+/g, '_');
  if (UZBEK_VALUE_DICTIONARY[normalized]) {
    return UZBEK_VALUE_DICTIONARY[normalized];
  }

  return val;
};

// Smart Label Cleaner for clean readability on charts (incorporates Uzbek localization)
export const cleanChartLabel = (val, maxLen = 18) => {
  if (val === null || val === undefined) return '';
  if (typeof val === 'number') {
    if (Math.abs(val) >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`;
    if (Math.abs(val) >= 1_000) return `${(val / 1_000).toFixed(0)}k`;
    return String(val);
  }

  // Humanize English DB enums to natural Uzbek first
  const humanized = humanizeUzbekValue(val);
  let str = String(humanized).trim();
  if (!str) return '';

  // Handle patterns like "10-sex (Xlor sexi)" or "120-sex (120 - Qurilish montaj...)"
  const parenMatch = str.match(/^([^(]+)\s*\(([^)]+)\)/);
  if (parenMatch) {
    const code = parenMatch[1].trim(); // e.g. "10-sex", "003-sex"
    const inside = parenMatch[2].trim(); // e.g. "Xlor sexi"
    const insideClean = inside.replace(/^\d+\s*[-–—]\s*/, '').trim();
    const firstWord = insideClean.split(/[\s,.-]+/)[0] || '';
    if (firstWord && firstWord.length <= 10) {
      const combined = `${code} (${firstWord})`;
      if (combined.length <= maxLen + 4) return combined;
    }
    if (code.length <= maxLen) return code;
  }

  if (str.length > maxLen) {
    return str.slice(0, maxLen - 1) + '…';
  }
  return str;
};

// Uzbek translation mapping for database column names
export const humanizeUzbekColumn = (colName) => {
  if (!colName) return '';
  const clean = String(colName).toLowerCase().trim();
  const dict = {
    // Finance / Quantities
    total_price: 'Jami Summa',
    price: 'Narx',
    amount: 'Miqdor',
    sum: 'Summa',
    jami_summa: 'Jami Summa',
    qiymat: 'Qiymat',
    soni: 'Soni',
    count: 'Soni',
    total_count: 'Jami Soni',
    total: 'Jami',
    capacity: "Sig'imi",
    warehouse_capacity: "Ombor Sig'imi",
    measure_unit: "O'lchov Birligi",
    unit: 'Birlik',

    // Dates / Times
    datetime_created: 'Yaratilgan Sana',
    created_at: 'Yaratilgan Vaqt',
    updated_at: 'Yangilangan Vaqt',
    date: 'Sana',
    oy: 'Oy / Davr',
    davr: 'Davr',
    kun: 'Kun',
    year: 'Yil',
    inspection_date: "Ko'rik Sanasi",
    trip_date: 'Reys Sanasi',
    start_date: 'Boshlanish Sanasi',
    end_date: 'Tugash Sanasi',

    // Status / Types / Enums
    status: 'Holati',
    state: 'Holati',
    current_status: 'Joriy Holati',
    ownership_type: 'Egalik Shakli',
    category: 'Toifa / Kategoriya',
    transport_type: 'Transport Turi',
    type: 'Toifa / Turi',
    toifa: 'Toifa',
    priority: 'Muhimlik Darajasi',
    condition: 'Texnik Holati',
    inspection_type: "Ko'rik Turi",
    ref_type: 'Hujjat Turi',
    waybill_type: "Yo'l Varaqasi Turi",
    calculation_mode: 'Hisoblash Rejimi',
    work_mode: 'Ish Rejimi',
    driver_availability_status: 'Haydovchi Holati',
    employment_status: 'Bandlik Holati',
    part_type: 'Qism Turi',
    is_admitted: 'Ruxsat Berilganligi',

    // Names / Entities
    name: 'Nomi',
    nomi: 'Nomi',
    warehouse_name: 'Ombor Nomi',
    district_name: 'Tuman Nomi',
    region_name: 'Viloyat Nomi',
    organization_name: 'Tashkilot Nomi',
    product_name: 'Mahsulot Nomi',
    driver_name: 'Haydovchi Ismi',
    full_name: "To'liq Ism (F.I.SH)",
    fio: 'F.I.SH',
    brand: 'Brend / Rusum',
    model: 'Model / Rusum',
    make: 'Marka',
    vehicle_number: 'Davlat Raqami',
    reg_number: 'Davlat Raqami',
    gov_number: 'Davlat Raqami',
    garage_number: 'Garaj Raqami',
    phone: 'Telefon Raqami',
    phone_number: 'Telefon Raqami',

    // Transport metrics
    fuel_amount: "Yoqilg'i Sarfi (L)",
    fuel_type: "Yoqilg'i Turi",
    fuel_consumed_liters: "Sarflangan Yoqilg'i (L)",
    fuel_issued_liters: "Berilgan Yoqilg'i (L)",
    distance: 'Masofa (km)',
    actual_distance_km: 'Haqiqiy Masofa (km)',
    planned_distance_km: 'Rejadagi Masofa (km)',
    odometer: 'Spidometr (km)',
    start_odometer: 'Boshlang\'ich Spidometr (km)',
    end_odometer: 'Yakuniy Spidometr (km)',
    motohours: 'Moto-soat',
    start_motohours: 'Boshlang\'ich Moto-soat',
    end_motohours: 'Yakuniy Moto-soat',
    tonnage: "Yuk Ko'tarish (t)",

    // Identifiers
    organization_id: 'Tashkilot ID',
    warehouse_id: 'Ombor ID',
    zavod_id: 'Zavod ID',
    driver_id: 'Haydovchi ID',
    vehicle_id: 'Transport ID',
    waybill_id: "Yo'l Varaqasi ID",
    trip_id: 'Reys ID',
    request_id: 'Buyurtma ID',
    code: 'Kod',
    id: 'ID'
  };

  if (dict[clean]) return dict[clean];
  return String(colName).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
};

// High-end Custom Tooltip for Dark Enterprise UI
const CustomChartTooltip = ({ active, payload, label }) => {
  if (active && payload && payload.length) {
    const rawTitle = payload[0]?.payload?.fullName || payload[0]?.payload?.[Object.keys(payload[0]?.payload || {})[0]] || label;
    const fullTitle = humanizeUzbekValue(rawTitle);
    return (
      <div className="bg-[#0B1120]/95 backdrop-blur-xl border border-slate-700/80 p-3 rounded-xl shadow-2xl text-xs space-y-1.5 min-w-[160px] max-w-[320px]">
        {fullTitle !== undefined && fullTitle !== null && (
          <p className="font-bold text-white border-b border-slate-800 pb-1.5 text-[11px] font-mono leading-snug">
            {String(fullTitle)}
          </p>
        )}
        {payload.map((entry, index) => (
          <div key={`tip-${index}`} className="flex items-center justify-between space-x-3">
            <div className="flex items-center space-x-2">
              <span className="w-2.5 h-2.5 rounded-full ring-2 ring-white/10" style={{ backgroundColor: entry.color || entry.fill }} />
              <span className="text-slate-400">{humanizeUzbekColumn(entry.name) || 'Qiymat'}:</span>
            </div>
            <span className="font-bold text-white font-mono">
              {typeof entry.value === 'number' ? entry.value.toLocaleString('uz-UZ') : humanizeUzbekValue(entry.value)}
            </span>
          </div>
        ))}
      </div>
    );
  }
  return null;
};


// Format axis tick
export const formatAxisTick = (val) => {
  if (typeof val === 'number') {
    if (Math.abs(val) >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`;
    if (Math.abs(val) >= 1_000) return `${(val / 1_000).toFixed(0)}k`;
  }
  return String(val).length > 18 ? String(val).slice(0, 16) + '...' : val;
};

// Automatic Smart Chart Axis Detection & Inversion Fixer
export const resolveChartAxes = (data, suggestedX, suggestedY) => {
  if (!data || data.length === 0) return { xKey: 'x', yKey: 'y', numericKeys: [], textKeys: [] };
  const sample = data[0] || {};
  const allKeys = Object.keys(sample);
  if (allKeys.length === 0) return { xKey: 'x', yKey: 'y', numericKeys: [], textKeys: [] };

  const numericKeys = allKeys.filter((k) => {
    return data.slice(0, 10).some((r) => {
      const val = r[k];
      return typeof val === 'number' || (!isNaN(parseFloat(val)) && val !== '' && val !== null);
    });
  });
  const textKeys = allKeys.filter((k) => !numericKeys.includes(k));

  let xKey = suggestedX;
  let yKey = suggestedY;

  // Auto-detect if invalid or missing
  if (!xKey || !allKeys.includes(xKey)) {
    xKey = textKeys[0] || allKeys[0];
  }
  if (!yKey || !allKeys.includes(yKey)) {
    yKey = numericKeys.find((k) => k !== xKey) || numericKeys[0] || allKeys.find((k) => k !== xKey) || allKeys[0];
  }

  // CRITICAL AUTO-SWAP: In charts, X must be the categorical label, and Y must be the numeric metric.
  // If xKey is numeric and yKey is textual, they were inverted by LLM -> SWAP THEM!
  if (numericKeys.includes(xKey) && textKeys.includes(yKey)) {
    const temp = xKey;
    xKey = yKey;
    yKey = temp;
  }

  // Force yKey to be numeric if any numeric key exists in data
  if (numericKeys.length > 0 && !numericKeys.includes(yKey)) {
    yKey = numericKeys[0];
    if (xKey === yKey && textKeys.length > 0) {
      xKey = textKeys[0];
    }
  }

  return { xKey, yKey, numericKeys, textKeys };
};

// High-end Sub-chart Card for Multi-Metric Composite Dashboards
const SUB_CHART_TYPES = ['horizontal_bar', 'donut', 'bar', 'area', 'line'];

function MultiChartCard({ chartSpec, index, total, globalCycle = 0 }) {
  const isFullWidth = (total % 2 === 1 && index === total - 1);
  const data = chartSpec.data || [];
  const keys = data[0] ? Object.keys(data[0]) : [];
  const isSingleRow = data.length === 1;

  // Auto-resolve and sanitize X and Y axes to guarantee X = Label, Y = Number
  const axes = useMemo(() => {
    return resolveChartAxes(data, chartSpec.x_axis, chartSpec.y_axis);
  }, [data, chartSpec.x_axis, chartSpec.y_axis]);

  const xKey = axes.xKey;
  const yKey = axes.yKey;

  // Calculate default diverse type so adjacent cards use different visual styles
  const defaultBaseType = useMemo(() => {
    if (isSingleRow) return 'metric';
    const raw = (chartSpec.chart_type || '').toLowerCase();
    if (SUB_CHART_TYPES.includes(raw)) return raw;
    return SUB_CHART_TYPES[index % SUB_CHART_TYPES.length];
  }, [chartSpec.chart_type, index, isSingleRow]);

  const [localType, setLocalType] = useState(null);

  // Active chart type derived from local selection, global cycle, or initial diverse type
  const ctype = useMemo(() => {
    if (isSingleRow) return 'metric';
    if (localType) return localType;
    if (globalCycle > 0) {
      const baseIdx = SUB_CHART_TYPES.indexOf(defaultBaseType);
      const safeBase = baseIdx >= 0 ? baseIdx : index;
      return SUB_CHART_TYPES[(safeBase + globalCycle) % SUB_CHART_TYPES.length];
    }
    return defaultBaseType;
  }, [isSingleRow, localType, globalCycle, defaultBaseType, index]);

  const handleNextType = () => {
    if (isSingleRow) return;
    const currIdx = SUB_CHART_TYPES.indexOf(ctype);
    const next = SUB_CHART_TYPES[(currIdx + 1) % SUB_CHART_TYPES.length];
    setLocalType(next);
  };

  const top10 = useMemo(() => (data || []).slice(0, 10), [data]);
  const rankingData = useMemo(() => {
    return [...data].sort((a, b) => (Number(b[yKey]) || 0) - (Number(a[yKey]) || 0)).slice(0, 10);
  }, [data, yKey]);

  const donutData = useMemo(() => {
    if (data.length <= 6) return data;
    const sorted = [...data].sort((a, b) => (Number(b[yKey]) || 0) - (Number(a[yKey]) || 0));
    const top5 = sorted.slice(0, 5);
    const others = sorted.slice(5);
    const othersSum = others.reduce((acc, r) => acc + (Number(r[yKey]) || 0), 0);
    if (othersSum > 0) {
      return [...top5, { [xKey]: 'Boshqalar', [yKey]: othersSum, fullName: `Boshqa barcha bo'limlar (${others.length} ta)`, _isOthers: true }];
    }
    return top5;
  }, [data, xKey, yKey]);

  const totalSum = useMemo(() => {
    return data.reduce((acc, r) => acc + (Number(r[yKey]) || 0), 0);
  }, [data, yKey]);

  let icon = <BarChart3 className="w-3.5 h-3.5 text-blue-400" />;
  let iconBg = 'bg-blue-500/10 text-blue-400';
  let badgeColor = 'bg-blue-500/10 text-blue-300 border-blue-500/20';

  if (ctype === 'donut' || ctype === 'pie') {
    icon = <PieIcon className="w-3.5 h-3.5 text-blue-400" />;
    iconBg = 'bg-blue-500/10 text-blue-400';
    badgeColor = 'bg-blue-500/10 text-blue-300 border-blue-500/20';
  } else if (ctype === 'horizontal_bar') {
    icon = <SlidersHorizontal className="w-3.5 h-3.5 text-amber-400" />;
    iconBg = 'bg-amber-500/10 text-amber-400';
    badgeColor = 'bg-amber-500/10 text-amber-300 border-amber-500/20';
  } else if (ctype === 'area') {
    icon = <TrendingUp className="w-3.5 h-3.5 text-cyan-400" />;
    iconBg = 'bg-cyan-500/10 text-cyan-400';
    badgeColor = 'bg-cyan-500/10 text-cyan-300 border-cyan-500/20';
  } else if (ctype === 'line') {
    icon = <Activity className="w-3.5 h-3.5 text-sky-400" />;
    iconBg = 'bg-sky-500/10 text-sky-400';
    badgeColor = 'bg-sky-500/10 text-sky-300 border-sky-500/20';
  }

  return (
    <div className={`${isFullWidth ? 'lg:col-span-2' : ''} bg-[#070B14]/90 p-4 rounded-2xl border border-[#1E293B] shadow-md space-y-2`}>
      <div className="flex items-center justify-between pb-1.5 border-b border-[#1E293B]/60 flex-wrap gap-y-1.5">
        <div className="flex items-center space-x-2">
          <div className={`w-6 h-6 rounded-lg ${iconBg} flex items-center justify-center`}>
            {icon}
          </div>
          <div>
            <h5 className="text-xs font-bold text-white tracking-tight">
              {index + 1}. {chartSpec.title}
            </h5>
            <p className="text-[10px] text-slate-400 font-mono">
              {humanizeUzbekColumn(yKey)} bo'yicha ({data.length} ta yozuv)
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-1.5">
          {!isSingleRow && (
            <div className="flex items-center bg-[#070B14] p-0.5 rounded-lg border border-[#1E293B] space-x-0.5">
              <button
                onClick={() => setLocalType('bar')}
                title="Ustunli grafik"
                className={`p-1 rounded transition ${ctype === 'bar' ? 'bg-blue-600 text-white shadow' : 'text-slate-400 hover:text-white'}`}
              >
                <BarChart3 className="w-3 h-3" />
              </button>
              <button
                onClick={() => setLocalType('donut')}
                title="Doiraviy grafik"
                className={`p-1 rounded transition ${ctype === 'donut' || ctype === 'pie' ? 'bg-blue-600 text-white shadow' : 'text-slate-400 hover:text-white'}`}
              >
                <PieIcon className="w-3 h-3" />
              </button>
              <button
                onClick={() => setLocalType('horizontal_bar')}
                title="TOP Reyting (Gorizontal)"
                className={`p-1 rounded transition ${ctype === 'horizontal_bar' ? 'bg-amber-600 text-white shadow' : 'text-slate-400 hover:text-white'}`}
              >
                <SlidersHorizontal className="w-3 h-3" />
              </button>
              <button
                onClick={() => setLocalType('area')}
                title="Dinamika (Soha)"
                className={`p-1 rounded transition ${ctype === 'area' ? 'bg-cyan-600 text-white shadow' : 'text-slate-400 hover:text-white'}`}
              >
                <TrendingUp className="w-3 h-3" />
              </button>
              <button
                onClick={() => setLocalType('line')}
                title="Chiziqli grafik"
                className={`p-1 rounded transition ${ctype === 'line' ? 'bg-sky-600 text-white shadow' : 'text-slate-400 hover:text-white'}`}
              >
                <Activity className="w-3 h-3" />
              </button>
              <button
                onClick={handleNextType}
                title="Keyingi grafik turiga aylantirish (Cycle)"
                className="p-1 rounded text-cyan-400 hover:bg-slate-800 transition border-l border-[#1E293B]/80 ml-0.5 pl-1.5"
              >
                <RotateCcw className="w-3 h-3" />
              </button>
            </div>
          )}

          <span className={`text-[9px] font-mono px-2 py-0.5 rounded-full border ${badgeColor}`}>
            {chartSpec.badge || ctype.toUpperCase()}
          </span>
        </div>
      </div>

      <div className="w-full pt-1">
        {data.length === 0 ? (
          <div className="h-44 flex items-center justify-center text-slate-500 text-xs">
            Ma\'lumot topilmadi
          </div>
        ) : (data.length === 1 && (ctype === 'metric' || ctype === 'kpi' || keys.length <= 2)) ? (
          <div className="h-44 flex flex-col items-center justify-center text-center space-y-1.5 bg-[#090F1B]/60 rounded-xl p-4">
            <span className="text-3xl sm:text-4xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-white via-cyan-100 to-blue-200 font-mono">
              {typeof data[0][yKey] === 'number' ? data[0][yKey].toLocaleString('uz-UZ') : humanizeUzbekValue(data[0][yKey] ?? data[0][xKey])}
            </span>
            <p className="text-xs text-slate-300 font-medium">{chartSpec.title}</p>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-sky-500/10 text-sky-300 border border-sky-500/25">
              Aniq Ko\'rsatkich
            </span>
          </div>
        ) : (ctype === 'donut' || ctype === 'pie') ? (
          <ResponsiveContainer width="100%" height={230}>
            <PieChart margin={{ top: 0, bottom: 0 }}>
              <Pie
                data={donutData}
                dataKey={yKey}
                nameKey={xKey}
                cx="50%"
                cy="42%"
                innerRadius={42}
                outerRadius={68}
                paddingAngle={3}
              >
                {donutData.map((entry, idx) => (
                  <Cell 
                    key={`donut-cell-${idx}`} 
                    fill={entry._isOthers ? '#64748B' : MULTI_COLORS[idx % MULTI_COLORS.length]} 
                    stroke="#0B0F19" 
                    strokeWidth={2} 
                  />
                ))}
              </Pie>
              <Tooltip content={<CustomChartTooltip />} />
              <Legend
                verticalAlign="bottom"
                wrapperStyle={{ fontSize: '10px', paddingTop: '6px' }}
                formatter={(val) => {
                  const item = donutData.find(r => String(r[xKey]) === String(val) || String(humanizeUzbekValue(r[xKey])) === String(val));
                  const valNum = Number(item?.[yKey]) || 0;
                  const pct = totalSum > 0 ? ((valNum / totalSum) * 100).toFixed(1) : 0;
                  const cleanName = cleanChartLabel(val, 16);
                  return <span className="text-slate-300 font-mono text-[10px] mr-1.5">{cleanName} ({pct}%)</span>;
                }}
              />
            </PieChart>
          </ResponsiveContainer>
        ) : ctype === 'horizontal_bar' ? (
          <ResponsiveContainer width="100%" height={230}>
            <BarChart layout="vertical" data={rankingData} margin={{ top: 4, right: 16, left: 10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1A2234" horizontal={false} />
              <XAxis type="number" stroke="#64748B" fontSize={10} tickLine={false} tickFormatter={formatAxisTick} />
              <YAxis 
                type="category" 
                dataKey={xKey} 
                stroke="#94A3B8" 
                fontSize={10} 
                tickLine={false} 
                width={120} 
                tickFormatter={(val) => cleanChartLabel(val, 16)} 
              />
              <Tooltip content={<CustomChartTooltip />} />
              <Bar dataKey={yKey} fill="#F59E0B" radius={[0, 4, 4, 0]} maxBarSize={16}>
                {rankingData.map((_, idx) => (
                  <Cell key={`hbar-cell-${idx}`} fill={MULTI_COLORS[idx % MULTI_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : ctype === 'area' ? (
          <ResponsiveContainer width="100%" height={230}>
            <AreaChart data={top10} margin={{ top: 8, right: 12, left: -10, bottom: 25 }}>
              <defs>
                <linearGradient id={`areaGrad-${index}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#06B6D4" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#06B6D4" stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1A2234" vertical={false} />
              <XAxis 
                dataKey={xKey} 
                stroke="#64748B" 
                fontSize={9} 
                tickLine={false} 
                interval={0}
                angle={-22}
                textAnchor="end"
                height={38}
                tickFormatter={(val) => cleanChartLabel(val, 11)} 
              />
              <YAxis stroke="#64748B" fontSize={10} tickLine={false} tickFormatter={formatAxisTick} width={38} />
              <Tooltip content={<CustomChartTooltip />} />
              <Area type="monotone" dataKey={yKey} stroke="#06B6D4" strokeWidth={2.5} fillOpacity={1} fill={`url(#areaGrad-${index})`} />
            </AreaChart>
          </ResponsiveContainer>
        ) : ctype === 'line' ? (
          <ResponsiveContainer width="100%" height={230}>
            <LineChart data={top10} margin={{ top: 8, right: 16, left: -10, bottom: 25 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1A2234" vertical={false} />
              <XAxis 
                dataKey={xKey} 
                stroke="#64748B" 
                fontSize={9} 
                tickLine={false} 
                interval={0}
                angle={-20}
                textAnchor="end"
                height={38}
                tickFormatter={(val) => cleanChartLabel(val, 12)} 
              />
              <YAxis stroke="#64748B" fontSize={10} tickLine={false} tickFormatter={formatAxisTick} width={38} />
              <Tooltip content={<CustomChartTooltip />} />
              <Line
                type="monotone"
                dataKey={yKey}
                stroke="#38BDF8"
                strokeWidth={2.5}
                dot={{ r: 3.5, fill: '#38BDF8' }}
                activeDot={{ r: 6, fill: '#7DD3FC' }}
              />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <ResponsiveContainer width="100%" height={230}>
            <BarChart data={top10} margin={{ top: 8, right: 12, left: -10, bottom: 25 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1A2234" vertical={false} />
              <XAxis 
                dataKey={xKey} 
                stroke="#64748B" 
                fontSize={9} 
                tickLine={false} 
                interval={0}
                angle={-22}
                textAnchor="end"
                height={38}
                tickFormatter={(val) => cleanChartLabel(val, 11)} 
              />
              <YAxis stroke="#64748B" fontSize={10} tickLine={false} tickFormatter={formatAxisTick} width={38} />
              <Tooltip content={<CustomChartTooltip />} />
              <Bar dataKey={yKey} fill="#3B82F6" radius={[4, 4, 0, 0]} maxBarSize={26}>
                {top10.map((_, idx) => (
                  <Cell key={`vbar-cell-${idx}`} fill={MULTI_COLORS[idx % MULTI_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

export const formatCell = (val) => {
  if (val === null || val === undefined || val === '') return '-';
  if (typeof val === 'boolean') return humanizeUzbekValue(val);
  if (typeof val === 'number') return val.toLocaleString('uz-UZ');
  if (typeof val === 'string' && /^-?\d+(\.\d+)?$/.test(val.trim())) {
    const num = parseFloat(val);
    if (!isNaN(num)) return num.toLocaleString('uz-UZ');
  }
  return String(humanizeUzbekValue(val));
};


function DataTableCard({ title, badge, rows, columns }) {
  const [searchTerm, setSearchTerm] = useState('');
  const [rowsPerPage, setRowsPerPage] = useState(15);
  const [currentPage, setCurrentPage] = useState(1);
  const [sortCol, setSortCol] = useState(null);
  const [sortAsc, setSortAsc] = useState(true);

  const processedRows = useMemo(() => {
    let list = [...(rows || [])];
    if (searchTerm.trim()) {
      const q = searchTerm.toLowerCase();
      list = list.filter((row) =>
        Object.values(row).some((val) => {
          const raw = String(val ?? '').toLowerCase();
          const uz = String(humanizeUzbekValue(val) ?? '').toLowerCase();
          return raw.includes(q) || uz.includes(q);
        })
      );
    }
    if (sortCol) {
      list.sort((a, b) => {
        const valA = a[sortCol];
        const valB = b[sortCol];
        if (typeof valA === 'number' && typeof valB === 'number') {
          return sortAsc ? valA - valB : valB - valA;
        }
        return sortAsc
          ? String(valA ?? '').localeCompare(String(valB ?? ''))
          : String(valB ?? '').localeCompare(String(valA ?? ''));
      });
    }
    return list;
  }, [rows, searchTerm, sortCol, sortAsc]);

  const totalRows = processedRows.length;
  const isAllRows = rowsPerPage === -1;
  const totalPages = isAllRows ? 1 : Math.ceil(totalRows / rowsPerPage) || 1;
  const paginatedRows = isAllRows
    ? processedRows
    : processedRows.slice((currentPage - 1) * rowsPerPage, currentPage * rowsPerPage);

  const columnSummaries = useMemo(() => {
    const sums = {};
    if (!rows || rows.length === 0 || !columns) return sums;
    columns.forEach((col) => {
      let total = 0;
      let count = 0;
      let isNumeric = true;
      for (const row of rows) {
        const val = row[col];
        if (typeof val === 'number') {
          total += val;
          count++;
        } else if (typeof val === 'string' && /^-?\d+(\.\d+)?$/.test(val.trim())) {
          total += parseFloat(val);
          count++;
        } else if (val !== null && val !== undefined && val !== '') {
          isNumeric = false;
          break;
        }
      }
      if (isNumeric && count > 0) {
        sums[col] = { total, count };
      }
    });
    return sums;
  }, [rows, columns]);

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortAsc(!sortAsc);
    } else {
      setSortCol(col);
      setSortAsc(true);
    }
  };

  const handleExportCsv = () => {
    if (!rows || rows.length === 0) return;
    const headers = columns.map((c) => `"${humanizeUzbekColumn(c).replace(/"/g, '""')}"`).join(',');
    const csvRows = rows.map((r) =>
      columns.map((c) => `"${String(humanizeUzbekValue(r[c]) ?? '').replace(/"/g, '""')}"`).join(',')
    );
    const csvContent = 'data:text/csv;charset=utf-8,\uFEFF' + [headers, ...csvRows].join('\n');
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', `${title.replace(/\s+/g, '_')}_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="bg-[#0B101D] p-4 sm:p-5 rounded-2xl border border-[#1E293B] shadow-xl space-y-3.5">
      {/* Header & Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5">
        <div className="flex items-center space-x-2">
          <div className="w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400 flex items-center justify-center">
            <TableIcon className="w-3.5 h-3.5 text-amber-400" />
          </div>
          <div>
            <h4 className="text-xs sm:text-sm font-extrabold text-white tracking-tight">
              {title}
            </h4>
            <span className="text-[10px] text-slate-400 font-mono">
              {totalRows} ta yozuv
            </span>
          </div>
          {badge && (
            <span className="text-[9px] font-mono px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-300 border border-amber-500/20 ml-2">
              {badge}
            </span>
          )}
        </div>

        <div className="flex items-center space-x-2.5 flex-1 max-w-sm justify-end">
          <div className="relative flex-1">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => {
                setSearchTerm(e.target.value);
                setCurrentPage(1);
              }}
              placeholder="Jadvaldan qidirish..."
              className="w-full bg-[#070B14] text-xs text-white placeholder-slate-500 rounded-lg pl-8 pr-2.5 py-1.5 border border-[#1E293B] focus:outline-none focus:border-blue-500 font-medium"
            />
          </div>

          <select
            value={rowsPerPage}
            onChange={(e) => {
              setRowsPerPage(Number(e.target.value));
              setCurrentPage(1);
            }}
            className="bg-[#070B14] text-xs text-slate-200 border border-[#1E293B] rounded-lg px-2 py-1.5 focus:outline-none focus:border-blue-500 font-medium shrink-0"
          >
            <option value={10}>10 ta</option>
            <option value={15}>15 ta</option>
            <option value={25}>25 ta</option>
            <option value={-1}>Barchasi</option>
          </select>

          <button
            onClick={handleExportCsv}
            className="p-1.5 rounded-lg bg-[#070B14] hover:bg-slate-800 text-slate-300 hover:text-white border border-[#1E293B] transition"
            title="CSV yuklab olish"
          >
            <Download className="w-3.5 h-3.5 text-blue-400" />
          </button>
        </div>
      </div>

      {/* Table Container */}
      <div className="overflow-x-auto rounded-xl border border-[#1E293B] shadow-inner bg-[#070B14]">
        <table className="w-full text-left text-xs text-slate-300">
          <thead className="bg-[#09101E] text-slate-300 font-bold border-b border-[#1E293B]">
            <tr>
              <th className="py-2.5 px-3 text-center font-mono text-[11px] text-slate-500 w-12">#</th>
              {columns.map((col) => {
                const isSorted = sortCol === col;
                return (
                  <th
                    key={col}
                    onClick={() => handleSort(col)}
                    className="py-2.5 px-3.5 font-mono text-[11px] tracking-wider uppercase cursor-pointer hover:bg-slate-800/60 transition select-none group"
                  >
                    <div className="flex items-center space-x-1.5">
                      <span className="text-white font-bold">{humanizeUzbekColumn(col)}</span>
                      <ArrowUpDown className={`w-3 h-3 ${isSorted ? 'text-cyan-400' : 'text-slate-600 group-hover:text-slate-400'} transition`} />
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>

          <tbody className="divide-y divide-[#1E293B]/70 font-mono">
            {paginatedRows.length === 0 ? (
              <tr>
                <td colSpan={columns.length + 1} className="py-6 text-center text-slate-500 text-xs">
                  {searchTerm ? "Qidiruvingiz bo'yicha mos ma'lumot topilmadi." : "Bazada yozuvlar topilmadi."}
                </td>
              </tr>
            ) : (
              paginatedRows.map((row, rIdx) => {
                const rowNum = isAllRows ? rIdx + 1 : (currentPage - 1) * rowsPerPage + rIdx + 1;
                return (
                  <tr key={rIdx} className="hover:bg-[#0E1628]/80 transition">
                    <td className="py-2 px-3 text-center text-slate-500 font-mono text-[11px]">
                      {rowNum}
                    </td>
                    {columns.map((col) => {
                      const val = row[col];
                      const isNumeric = typeof val === 'number' || (typeof val === 'string' && /^-?\d+(\.\d+)?$/.test(val.trim()));
                      return (
                        <td
                          key={col}
                          className={`py-2 px-3.5 text-xs ${
                            isNumeric
                              ? 'text-right text-sky-400 font-semibold whitespace-nowrap w-28 shrink-0'
                              : 'text-slate-200 max-w-xs md:max-w-md xl:max-w-lg truncate hover:whitespace-normal break-words'
                          }`}
                          title={!isNumeric && val ? String(val) : undefined}
                        >
                          {formatCell(val)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })
            )}
          </tbody>

          {Object.keys(columnSummaries).length > 0 && paginatedRows.length > 0 && (
            <tfoot className="bg-[#09101E] border-t-2 border-[#1E293B] font-mono text-xs font-bold">
              <tr>
                <td className="py-2 px-3 text-center text-slate-400 uppercase text-[11px]">Jami</td>
                {columns.map((col) => {
                  const sumMeta = columnSummaries[col];
                  return (
                    <td
                      key={`sum-${col}`}
                      className={`py-2 px-3.5 whitespace-nowrap ${
                        sumMeta ? 'text-right text-cyan-300 font-bold' : 'text-slate-500'
                      }`}
                    >
                      {sumMeta ? formatCell(sumMeta.total) : '—'}
                    </td>
                  );
                })}
              </tr>
            </tfoot>
          )}
        </table>
      </div>

      {/* Pagination Controls */}
      {!isAllRows && totalPages > 1 && (
        <div className="flex items-center justify-between pt-1 text-xs">
          <span className="text-slate-400 text-xs">
            Sahifa: <strong className="text-white">{currentPage}</strong> / {totalPages} (Jami: {totalRows} ta)
          </span>

          <div className="flex items-center space-x-1.5">
            <button
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              disabled={currentPage === 1}
              className="p-1 rounded-md bg-[#070B14] hover:bg-slate-800 disabled:opacity-40 text-slate-300 border border-[#1E293B] transition"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>

            <button
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              disabled={currentPage === totalPages}
              className="p-1 rounded-md bg-[#070B14] hover:bg-slate-800 disabled:opacity-40 text-slate-300 border border-[#1E293B] transition"
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function QueryTestResultView({
  result,
  prompt,
  loading,
  onRequery,
  onClose
}) {
  // Modes: 'chart' (Visual Grafik), 'table' (Jadval)
  const [activeView, setActiveView] = useState('chart');
  const [searchTerm, setSearchTerm] = useState('');
  const [rowsPerPage, setRowsPerPage] = useState(15);
  const [currentPage, setCurrentPage] = useState(1);
  const [sortCol, setSortCol] = useState(null);
  const [sortAsc, setSortAsc] = useState(true);

  if (!result) return null;

  const chart = result.chart || {};
  const rawRows = result.rows && result.rows.length > 0 ? result.rows : (chart.data || []);
  const columns = result.columns && result.columns.length > 0 ? result.columns : (rawRows[0] ? Object.keys(rawRows[0]) : []);

  // Visual Chart Data & Multi-Chart Analytics
  const chartData = (chart.data && chart.data.length > 0) ? chart.data : rawRows;
  const multiCharts = (result.charts && result.charts.length > 1) ? result.charts : [];
  const isMultiChart = multiCharts.length > 1;
  const isSingleMetric = !isMultiChart && rawRows.length === 1 && columns.length <= 2;
  const defaultSingleType = (chart.chart_type && chart.chart_type !== 'table') ? chart.chart_type : 'bar';
  const [selectedChartType, setSelectedChartType] = useState(isSingleMetric ? 'single' : defaultSingleType);
  const [dashboardCycle, setDashboardCycle] = useState(0);

  // Derive sanitized and auto-corrected axes for chart (ensuring X = Label, Y = Number)
  const axes = useMemo(() => {
    return resolveChartAxes(chartData, chart.x_axis, chart.y_axis);
  }, [chartData, chart.x_axis, chart.y_axis]);

  const xKey = axes.xKey;
  const yKey = axes.yKey;
  const numericKeys = axes.numericKeys;
  const singleMetricValue = isSingleMetric
    ? rawRows[0][
        (chart.y_axis && Object.prototype.hasOwnProperty.call(rawRows[0], chart.y_axis))
          ? chart.y_axis
          : (numericKeys[0] || columns[columns.length - 1])
      ]
    : null;

  // Secondary Y-axis for multi-measure queries (e.g. fuel vs count, distance vs count)
  const secondaryYKey = numericKeys.length > 1 ? numericKeys[1] : yKey;

  // 1. Strict 10-item limit for all visual charts
  const top10Data = useMemo(() => {
    return chartData.slice(0, 10);
  }, [chartData]);

  // 2. Strict 10-item limit for TOP ranking (sorted descending)
  const rankingData = useMemo(() => {
    return [...chartData]
      .sort((a, b) => (Number(b[yKey]) || 0) - (Number(a[yKey]) || 0))
      .slice(0, 10);
  }, [chartData, yKey]);

  // 3. Smart Donut Data: Top 5 items + "Boshqalar" (Others)
  // Prevents text overflow, awkward wrapping, and legend clutter
  const donutData = useMemo(() => {
    if (!chartData || chartData.length === 0) return [];
    if (chartData.length <= 6) return chartData;

    const sorted = [...chartData].sort((a, b) => (Number(b[yKey]) || 0) - (Number(a[yKey]) || 0));
    const top5 = sorted.slice(0, 5);
    const others = sorted.slice(5);

    const othersSum = others.reduce((acc, r) => acc + (Number(r[yKey]) || 0), 0);
    if (othersSum > 0) {
      const otherRow = {
        [xKey]: 'Boshqalar',
        [yKey]: othersSum,
        fullName: `Boshqa barcha bo'limlar (${others.length} ta)`,
        _isOthers: true
      };
      return [...top5, otherRow];
    }
    return top5;
  }, [chartData, xKey, yKey]);

  // Executive KPI summary stats for multi-row data
  const kpiStats = useMemo(() => {
    if (!chartData || chartData.length === 0 || !yKey) return null;
    let sum = 0;
    let max = -Infinity;
    let min = Infinity;
    let count = 0;
    let maxLabel = '';

    chartData.forEach((r) => {
      const val = Number(r[yKey]);
      if (!isNaN(val)) {
        sum += val;
        count++;
        if (val > max) {
          max = val;
          maxLabel = String(r[xKey] ?? '');
        }
        if (val < min) min = val;
      }
    });

    const avg = count > 0 ? sum / count : 0;
    return {
      totalRecords: chartData.length,
      sum,
      avg,
      max: max === -Infinity ? 0 : max,
      maxLabel: cleanChartLabel(maxLabel, 18)
    };
  }, [chartData, xKey, yKey]);

  // Format axis tick
  const formatAxisTick = (val) => {
    if (typeof val === 'number') {
      if (Math.abs(val) >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`;
      if (Math.abs(val) >= 1_000) return `${(val / 1_000).toFixed(0)}k`;
    }
    return String(val).length > 18 ? String(val).slice(0, 16) + '...' : val;
  };

  // Filter & Sort table rows (searches both raw value and translated Uzbek text)
  const processedRows = useMemo(() => {
    let list = [...rawRows];

    if (searchTerm.trim()) {
      const q = searchTerm.toLowerCase();
      list = list.filter((row) =>
        Object.values(row).some((val) => {
          const raw = String(val ?? '').toLowerCase();
          const uz = String(humanizeUzbekValue(val) ?? '').toLowerCase();
          return raw.includes(q) || uz.includes(q);
        })
      );
    }

    if (sortCol) {
      list.sort((a, b) => {
        const valA = a[sortCol];
        const valB = b[sortCol];
        if (typeof valA === 'number' && typeof valB === 'number') {
          return sortAsc ? valA - valB : valB - valA;
        }
        return sortAsc
          ? String(valA ?? '').localeCompare(String(valB ?? ''))
          : String(valB ?? '').localeCompare(String(valA ?? ''));
      });
    }

    return list;
  }, [rawRows, searchTerm, sortCol, sortAsc]);

  // Pagination calculation
  const totalRows = processedRows.length;
  const isAllRows = rowsPerPage === -1;
  const totalPages = isAllRows ? 1 : Math.ceil(totalRows / rowsPerPage) || 1;
  const paginatedRows = isAllRows
    ? processedRows
    : processedRows.slice((currentPage - 1) * rowsPerPage, currentPage * rowsPerPage);

  // Column summaries for numeric columns (Sum)
  const columnSummaries = useMemo(() => {
    const sums = {};
    columns.forEach((col) => {
      let isNumeric = true;
      let total = 0;
      let count = 0;
      for (const row of rawRows) {
        const val = row[col];
        if (typeof val === 'number') {
          total += val;
          count++;
        } else if (typeof val === 'string' && /^-?\d+(\.\d+)?$/.test(val.trim())) {
          total += parseFloat(val);
          count++;
        } else if (val !== null && val !== undefined && val !== '') {
          isNumeric = false;
          break;
        }
      }
      if (isNumeric && count > 0) {
        sums[col] = { total, count };
      }
    });
    return sums;
  }, [rawRows, columns]);

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortAsc(!sortAsc);
    } else {
      setSortCol(col);
      setSortAsc(true);
    }
  };

  const handleExportCsv = () => {
    if (!rawRows || rawRows.length === 0) return;
    const headers = columns.map((c) => `"${humanizeUzbekColumn(c).replace(/"/g, '""')}"`).join(',');
    const rows = rawRows.map((r) =>
      columns.map((c) => `"${String(humanizeUzbekValue(r[c]) ?? '').replace(/"/g, '""')}"`).join(',')
    );
    const csvContent = 'data:text/csv;charset=utf-8,\uFEFF' + [headers, ...rows].join('\n');
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', `tabloai_export_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="space-y-6 animate-fadeIn">
      
      {/* 1. Header Control Bar */}
      <div className="bg-[#0D1527] p-5 rounded-3xl border border-blue-500/40 shadow-2xl shadow-blue-500/5 space-y-4">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          
          {/* Query title and status */}
          <div className="space-y-1.5">
            <div className="flex items-center space-x-2.5 flex-wrap gap-y-1">
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse" />
              <span className="text-[11px] uppercase font-mono font-bold tracking-wider text-emerald-400 flex items-center space-x-1">
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>Bazadan Aniq Ma'lumot Olindi</span>
              </span>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-[#080C16] text-slate-300 border border-[#1E293B]">
                {result.execution_time_ms.toFixed(0)} ms
              </span>
              {isMultiChart ? (
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-300 border border-blue-500/30">
                  {multiCharts.length} ta Yo'nalish
                </span>
              ) : (
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-300 border border-blue-500/30">
                  Jami: {rawRows.length} ta yozuv
                </span>
              )}
              {result.table_used && (
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-[#080C16] text-cyan-300 border border-[#1E293B]">
                  Jadval: {result.table_used}
                </span>
              )}
            </div>

            <h3 className="text-lg sm:text-xl font-extrabold text-white tracking-tight">
              "{prompt || chart.title || 'So\'rov natijasi'}"
            </h3>
            {(result.is_truncated || result.validation_warnings?.length > 0) && (
              <div className="flex items-start gap-2 text-[11px] text-amber-300">
                <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span>
                  {result.validation_warnings?.[0] || "Natija chegaralangan; ko'rsatilgan qatorlar jami son emas."}
                </span>
              </div>
            )}
          </div>

          {/* Action Buttons & View Mode Tabs */}
          <div className="flex items-center space-x-2 shrink-0 flex-wrap gap-y-2">
            
            {/* 2 Core Section View Pills */}
            <div className="flex items-center bg-[#070B14] p-1 rounded-xl border border-[#1E293B]">
              <button
                onClick={() => setActiveView('chart')}
                className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                  activeView === 'chart'
                    ? 'bg-blue-600 text-white shadow-md'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                <BarChart3 className="w-3.5 h-3.5 text-blue-400" />
                <span>Visual Grafik</span>
              </button>

              <button
                onClick={() => setActiveView('table')}
                className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                  activeView === 'table'
                    ? 'bg-blue-600 text-white shadow-md'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                <TableIcon className="w-3.5 h-3.5 text-amber-400" />
                <span>Jadval</span>
              </button>
            </div>

            {/* CSV Download */}
            <button
              onClick={handleExportCsv}
              className="px-3 py-1.5 rounded-xl bg-[#080C16] hover:bg-slate-800 text-slate-300 hover:text-white border border-[#1E293B] text-xs font-semibold transition flex items-center space-x-1.5"
              title="Excel / CSV formatida yuklab olish"
            >
              <Download className="w-3.5 h-3.5 text-blue-400" />
              <span className="hidden sm:inline">CSV</span>
            </button>

            {/* Re-calculate */}
            <button
              onClick={() => onRequery && onRequery(prompt)}
              disabled={loading}
              className="p-2 rounded-xl bg-[#080C16] hover:bg-slate-800 text-slate-300 hover:text-white border border-[#1E293B] text-xs transition"
              title="Qayta hisoblash"
            >
              <RotateCcw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>

            {/* Close */}
            {onClose && (
              <button
                onClick={onClose}
                className="p-2 rounded-xl bg-slate-800/80 hover:bg-rose-900/40 text-slate-300 hover:text-rose-300 border border-slate-700 text-xs transition"
                title="Natijani tozalash"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

        </div>
      </div>

      {/* 2. SECTION: VISUAL GRAFIK */}
      {activeView === 'chart' && (
        <div className="bg-[#0B101D] p-4 sm:p-5 rounded-2xl border border-[#1E293B] shadow-xl space-y-4">
          
          {/* Chart Header & Type Switcher */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 border-b border-[#1E293B]/80 pb-3">
            <div className="flex items-center space-x-2.5">
              <div className="w-8 h-8 rounded-lg bg-blue-500/10 border border-blue-500/20 text-blue-400 flex items-center justify-center">
                <BarChart3 className="w-4 h-4 text-cyan-400" />
              </div>
              <div>
                <h4 className="text-xs sm:text-sm font-extrabold text-white tracking-tight">
                  Vizual Grafik & Taqsimot
                </h4>
                <span className="text-[10px] text-slate-400 font-mono">
                  {chart.title || prompt}
                </span>
              </div>
            </div>

            {/* Quick Chart Type Buttons (Single queries allow switching view; Multi-chart shows dashboard badge) */}
            {!isSingleMetric && !isMultiChart && (
              <div className="flex items-center space-x-1 bg-[#070B14] p-1 rounded-xl border border-[#1E293B] self-start sm:self-center flex-wrap gap-y-1">
                <button
                  onClick={() => setSelectedChartType('bar')}
                  className={`px-2.5 py-1 rounded-lg text-xs font-semibold transition flex items-center space-x-1 ${
                    selectedChartType === 'bar' ? 'bg-blue-600 text-white shadow' : 'text-slate-400 hover:text-white'
                  }`}
                  title="Ustunli miqdoriy taqqoslash"
                >
                  <BarChart3 className="w-3 h-3 text-blue-400" />
                  <span>Ustunli</span>
                </button>

                <button
                  onClick={() => setSelectedChartType('donut')}
                  className={`px-2.5 py-1 rounded-lg text-xs font-semibold transition flex items-center space-x-1 ${
                    selectedChartType === 'donut' || selectedChartType === 'pie' ? 'bg-blue-600 text-white shadow' : 'text-slate-400 hover:text-white'
                  }`}
                  title="Doiraviy taqsimot va ulushlar"
                >
                  <PieIcon className="w-3 h-3 text-blue-400" />
                  <span>Doiraviy</span>
                </button>

                <button
                  onClick={() => setSelectedChartType('horizontal_bar')}
                  className={`px-2.5 py-1 rounded-lg text-xs font-semibold transition flex items-center space-x-1 ${
                    selectedChartType === 'horizontal_bar' ? 'bg-blue-600 text-white shadow' : 'text-slate-400 hover:text-white'
                  }`}
                  title="TOP liderlar va reyting"
                >
                  <SlidersHorizontal className="w-3 h-3 text-amber-400" />
                  <span>TOP Reyting</span>
                </button>

                <button
                  onClick={() => setSelectedChartType('area')}
                  className={`px-2.5 py-1 rounded-lg text-xs font-semibold transition flex items-center space-x-1 ${
                    selectedChartType === 'area' ? 'bg-blue-600 text-white shadow' : 'text-slate-400 hover:text-white'
                  }`}
                  title="Oqim va hajm dinamikasi"
                >
                  <TrendingUp className="w-3 h-3 text-cyan-400" />
                  <span>Dinamika</span>
                </button>

                <button
                  onClick={() => setSelectedChartType('line')}
                  className={`px-2.5 py-1 rounded-lg text-xs font-semibold transition flex items-center space-x-1 ${
                    selectedChartType === 'line' ? 'bg-blue-600 text-white shadow' : 'text-slate-400 hover:text-white'
                  }`}
                  title="Chiziqli tebranish va trend"
                >
                  <Activity className="w-3 h-3 text-sky-400" />
                  <span>Chiziqli</span>
                </button>
              </div>
            )}

            {isMultiChart && (
              <div className="flex items-center space-x-2 flex-wrap gap-y-1">
                <button
                  onClick={() => setDashboardCycle((prev) => prev + 1)}
                  className="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-300 border border-cyan-500/25 text-xs font-semibold transition"
                  title="Barcha grafiklarni 6 xil tur bo'yicha aylanma almashtirish"
                >
                  <RotateCcw className="w-3.5 h-3.5 text-cyan-400" />
                  <span>Grafiklarni Aylantirish (6 tur)</span>
                </button>
                <div className="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl bg-blue-500/10 border border-blue-500/25 text-blue-300 text-xs font-semibold">
                  <Sparkles className="w-3.5 h-3.5 text-cyan-400" />
                  <span>{multiCharts.length} ta Mustaqil Grafik</span>
                </div>
              </div>
            )}
          </div>

          {/* Chart Rendering Body */}
          <div className="w-full pt-0.5">
            {isMultiChart ? (
              /* ======================================================== */
              /* DYNAMIC MULTI-METRIC ANALYTICS DASHBOARD GRID            */
              /* ======================================================== */
              <div className="space-y-4">
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  {multiCharts.map((cSpec, idx) => (
                    <MultiChartCard
                      key={cSpec.id || idx}
                      chartSpec={cSpec}
                      index={idx}
                      total={multiCharts.length}
                      globalCycle={dashboardCycle}
                    />
                  ))}
                </div>
              </div>
            ) : isSingleMetric ? (
              /* Executive Single Value KPI / Metric Card - Compact & Sharp */
              <div className="bg-gradient-to-b from-[#0B1322] via-[#090F1B] to-[#070B14] py-6 px-6 rounded-2xl border border-cyan-500/25 flex flex-col items-center justify-center text-center space-y-2 relative overflow-hidden shadow-lg shadow-cyan-950/20">
                <div className="absolute -top-10 -right-10 w-40 h-40 bg-cyan-500/5 rounded-full blur-2xl pointer-events-none" />
                <div className="w-10 h-10 rounded-xl bg-cyan-500/10 border border-cyan-500/30 text-cyan-400 flex items-center justify-center shadow-md shadow-cyan-500/10 mb-0.5">
                  <Activity className="w-5 h-5 text-cyan-300 animate-pulse" />
                </div>
                <div className="space-y-0.5">
                  <span className="text-3xl sm:text-4xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-white via-cyan-100 to-blue-200 font-mono tracking-tight">
                    {formatCell(singleMetricValue)}
                  </span>
                  <p className="text-xs text-slate-300 font-semibold max-w-sm mx-auto leading-snug">
                    {prompt}
                  </p>
                </div>
                <span className="text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-sky-500/10 text-sky-300 border border-sky-500/25 mt-0.5">
                  Aniq Natija
                </span>
              </div>
            ) : chartData.length === 0 ? (
              <div className="h-44 flex flex-col items-center justify-center text-slate-500 text-xs">
                <Activity className="w-7 h-7 text-slate-600 mb-1.5" />
                <p>Grafik uchun ma'lumot topilmadi.</p>
              </div>
            ) : selectedChartType === 'horizontal_bar' ? (
              /* Single Focused Horizontal Bar Chart */
              <ResponsiveContainer width="100%" height={290}>
                <BarChart layout="vertical" data={rankingData} margin={{ top: 8, right: 24, left: 20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1A2234" horizontal={false} />
                  <XAxis type="number" stroke="#64748B" fontSize={11} tickLine={false} tickFormatter={formatAxisTick} />
                  <YAxis type="category" dataKey={xKey} stroke="#94A3B8" fontSize={11} tickLine={false} width={130} tickFormatter={(val) => cleanChartLabel(val, 18)} />
                  <Tooltip content={<CustomChartTooltip />} />
                  <Bar dataKey={yKey} fill="#3B82F6" radius={[0, 4, 4, 0]} maxBarSize={22}>
                    {rankingData.map((_, idx) => (
                      <Cell key={`hbar-${idx}`} fill={MULTI_COLORS[idx % MULTI_COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (selectedChartType === 'donut' || selectedChartType === 'pie') ? (
              /* Single Focused Donut / Pie Chart */
              <ResponsiveContainer width="100%" height={280}>
                <PieChart>
                  <Pie
                    data={donutData}
                    dataKey={yKey}
                    nameKey={xKey}
                    cx="50%"
                    cy="50%"
                    innerRadius={56}
                    outerRadius={88}
                    paddingAngle={3}
                  >
                    {donutData.map((_, idx) => (
                      <Cell key={`pie-${idx}`} fill={MULTI_COLORS[idx % MULTI_COLORS.length]} stroke="#0B0F19" strokeWidth={2} />
                    ))}
                  </Pie>
                  <Tooltip content={<CustomChartTooltip />} />
                  <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '6px' }} formatter={(val) => <span className="text-slate-300 font-mono text-[11px]">{cleanChartLabel(val, 18)}</span>} />
                </PieChart>
              </ResponsiveContainer>
            ) : selectedChartType === 'area' ? (
              /* Single Focused Smooth Area Chart */
              <ResponsiveContainer width="100%" height={290}>
                <AreaChart data={top10Data} margin={{ top: 8, right: 16, left: 0, bottom: 30 }}>
                  <defs>
                    <linearGradient id="areaGradientSingle" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#06B6D4" stopOpacity={0.4} />
                      <stop offset="95%" stopColor="#06B6D4" stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1A2234" vertical={false} />
                  <XAxis 
                    dataKey={xKey} 
                    stroke="#64748B" 
                    fontSize={10} 
                    tickLine={false} 
                    interval={0}
                    angle={-22}
                    textAnchor="end"
                    height={40}
                    tickFormatter={(val) => cleanChartLabel(val, 13)} 
                  />
                  <YAxis stroke="#64748B" fontSize={11} tickLine={false} tickFormatter={formatAxisTick} width={40} />
                  <Tooltip content={<CustomChartTooltip />} />
                  <Area type="monotone" dataKey={secondaryYKey || yKey} stroke="#06B6D4" strokeWidth={2.5} fillOpacity={1} fill="url(#areaGradientSingle)" />
                </AreaChart>
              </ResponsiveContainer>
            ) : selectedChartType === 'line' ? (
              /* Single Focused Line Chart */
              <ResponsiveContainer width="100%" height={290}>
                <LineChart data={top10Data} margin={{ top: 8, right: 16, left: 0, bottom: 30 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1A2234" vertical={false} />
                  <XAxis 
                    dataKey={xKey} 
                    stroke="#64748B" 
                    fontSize={10} 
                    tickLine={false} 
                    interval={0}
                    angle={-20}
                    textAnchor="end"
                    height={40}
                    tickFormatter={(val) => cleanChartLabel(val, 13)} 
                  />
                  <YAxis stroke="#64748B" fontSize={11} tickLine={false} tickFormatter={formatAxisTick} width={40} />
                  <Tooltip content={<CustomChartTooltip />} />
                  <Line type="monotone" dataKey={yKey} stroke="#38BDF8" strokeWidth={2.5} dot={{ r: 3.5, fill: '#38BDF8' }} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              /* Single Focused Standard Vertical Bar Chart */
              <ResponsiveContainer width="100%" height={290}>
                <BarChart data={top10Data} margin={{ top: 8, right: 16, left: 0, bottom: 30 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1A2234" vertical={false} />
                  <XAxis 
                    dataKey={xKey} 
                    stroke="#64748B" 
                    fontSize={10} 
                    tickLine={false} 
                    interval={0}
                    angle={-22}
                    textAnchor="end"
                    height={40}
                    tickFormatter={(val) => cleanChartLabel(val, 13)} 
                  />
                  <YAxis stroke="#64748B" fontSize={11} tickLine={false} tickFormatter={formatAxisTick} width={40} />
                  <Tooltip content={<CustomChartTooltip />} />
                  <Bar dataKey={yKey} fill="#3B82F6" radius={[4, 4, 0, 0]} maxBarSize={36}>
                    {chartData.slice(0, 25).map((_, idx) => (
                      <Cell key={`bar-${idx}`} fill={MULTI_COLORS[idx % MULTI_COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

        </div>
      )}

      {/* 3. SECTION: JADVAL (TO'LIQ MA'LUMOTLAR JADVALI) */}
      {activeView === 'table' && (
        <div className="space-y-6">
          {isMultiChart ? (
            multiCharts.map((cSpec, idx) => {
              const cRows = cSpec.data || [];
              const cCols = cRows[0] ? Object.keys(cRows[0]) : [];
              return (
                <DataTableCard
                  key={cSpec.id || idx}
                  title={`${idx + 1}. ${cSpec.title}`}
                  badge={cSpec.badge || `Jadval #${idx + 1}`}
                  rows={cRows}
                  columns={cCols}
                />
              );
            })
          ) : (
            <DataTableCard
              title="Batafsil Ma'lumotlar Jadvali"
              badge={result.table_used || "Jadval"}
              rows={rawRows}
              columns={columns}
            />
          )}
        </div>
      )}

    </div>
  );
}
