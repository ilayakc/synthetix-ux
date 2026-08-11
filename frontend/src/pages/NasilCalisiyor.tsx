import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { applyPublicTheme, getPublicTheme, type PublicTheme } from "../theme/publicTheme";
import BrandLogo from "../components/BrandLogo";

// Uygulama ici "Nasil calisiyor?" tanitim sayfasi. Icerik, ayri olarak
// hazirlanan sessiz/otomatik tur ile ayni; renkler uygulama token'larina
// (tokens.css) baglandigi icin acik/koyu temayla otomatik uyumludur. Tum
// siniflar `hc-` on ekiyle isimlendirildiginden global stillerle cakismaz.
const CSS = `
.hc-page{
  --accent: var(--color-accent);
  --accent-hover: var(--color-accent-hover);
  --accent-soft: var(--color-accent-soft);
  --accent-contrast: var(--color-accent-contrast);
  --surface: var(--color-surface);
  --surface-2: var(--color-bg);
  --border: var(--color-border);
  --text: var(--color-text);
  --muted: var(--color-text-muted);
  --bg: var(--color-bg);
  --success-bg: var(--color-success-bg);
  --success-border: var(--color-success-border);
  --success-text: var(--color-success-text);
  --warn-bg: var(--color-warning-bg);
  --warn-text: var(--color-warning-text);
  --hot: var(--color-series-b);
  --hot-2: var(--color-series-b);
  --shadow: var(--shadow-card);
  --r-sm: var(--radius-sm);
  --r-md: var(--radius-md);
  --r-lg: var(--radius-lg);
  --mono: ui-monospace, "Cascadia Mono", Consolas, "Liberation Mono", monospace;
  background: var(--bg);
  color: var(--text);
  min-height: 100%;
  font-family: var(--font-sans);
  line-height: 1.6;
}
.hc-page *{ box-sizing: border-box; }
.hc-wrap{ max-width: 960px; margin: 0 auto; padding: 0 20px; }
.hc-page h1,.hc-page h2,.hc-page h3{ margin: 0; line-height: 1.15; letter-spacing: -.015em; }
.hc-page p{ margin: 0; }
.hc-eyebrow{ font-size: .78rem; letter-spacing: .04em; color: var(--accent); font-weight: 650; }
.hc-mono{ font-family: var(--mono); }

.hc-top{ display:flex; align-items:center; justify-content:space-between; height:56px; gap:12px;
  border-bottom:1px solid var(--border); }
.hc-brand{ display:flex; align-items:center; gap:10px; font-weight:750; letter-spacing:-.01em;
  color:var(--text); text-decoration:none; min-width:0; }
.hc-sep{ color:var(--muted); font-weight:500; font-size:.92rem; white-space:nowrap; }
@media(max-width:520px){ .hc-sep{ display:none; } }
.hc-theme{ display:inline-flex; gap:4px; background:var(--surface); border:1px solid var(--border);
  border-radius:999px; padding:3px; }
.hc-theme button{ width:32px; height:30px; border:none; background:transparent; border-radius:999px;
  cursor:pointer; color:var(--muted); font-size:.95rem; display:grid; place-items:center; line-height:1; }
.hc-theme button[aria-pressed="true"]{ background:var(--accent-soft); color:var(--accent); }

.hc-hero{ padding:44px 0 20px; text-align:center; }
.hc-hero h1{ font-size:clamp(1.9rem,5vw,3rem); font-weight:800; max-width:18ch; margin:14px auto 0; }
.hc-sub{ margin:16px auto 0; max-width:58ch; color:var(--muted); font-size:1.05rem; }

.hc-player{ margin:22px auto 0; max-width:900px; }
.hc-frame{ background:var(--surface); border:1px solid var(--border); border-radius:var(--r-lg);
  box-shadow:var(--shadow); overflow:hidden; }
.hc-chrome{ display:flex; align-items:center; gap:10px; padding:11px 14px; border-bottom:1px solid var(--border);
  background:var(--surface-2); }
.hc-dots{ display:flex; gap:6px; }
.hc-dots i{ width:11px; height:11px; border-radius:50%; background:var(--border); }
.hc-addr{ flex:1; font-family:var(--mono); font-size:.74rem; color:var(--muted); background:var(--bg);
  border:1px solid var(--border); border-radius:999px; padding:5px 12px; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; }
.hc-tc{ font-family:var(--mono); font-size:.72rem; color:var(--muted); font-variant-numeric:tabular-nums; }

.hc-stage{ position:relative; aspect-ratio:16/10; background:var(--bg); overflow:hidden; }
@media(max-width:600px){ .hc-stage{ aspect-ratio:3/4; } }
.hc-app{ position:absolute; inset:0; display:grid; grid-template-columns:158px 1fr; }
.hc-side{ background:var(--surface-2); border-right:1px solid var(--border); padding:12px 10px;
  display:flex; flex-direction:column; gap:2px; overflow:hidden; }
.hc-ws{ font-size:.62rem; letter-spacing:.08em; text-transform:uppercase; color:var(--muted);
  padding:4px 10px 8px; font-weight:650; }
.hc-side a{ display:block; padding:7px 10px; border-radius:var(--r-sm); font-size:.78rem; color:var(--muted);
  text-decoration:none; }
.hc-side a.hc-active{ background:var(--accent-soft); color:var(--accent); font-weight:650; }
.hc-grp{ font-size:.6rem; letter-spacing:.08em; text-transform:uppercase; color:var(--muted);
  padding:10px 10px 4px; opacity:.8; }
.hc-spacer{ flex:1; }
@media(max-width:600px){ .hc-app{ grid-template-columns:1fr; } .hc-side{ display:none; } }

.hc-main{ display:flex; flex-direction:column; min-width:0; }
.hc-topbar{ height:42px; border-bottom:1px solid var(--border); display:flex; align-items:center;
  justify-content:space-between; padding:0 14px; flex:none; background:var(--surface); }
.hc-topbar .hc-ttl{ font-weight:700; font-size:.9rem; }
.hc-tr{ display:flex; align-items:center; gap:10px; }
.hc-newbtn{ font-size:.72rem; font-weight:650; color:var(--accent-contrast); background:var(--accent);
  border-radius:var(--r-sm); padding:6px 11px; }
.hc-ava{ width:24px; height:24px; border-radius:50%; background:var(--accent-soft); border:1px solid var(--border); }

.hc-content{ position:relative; flex:1; overflow:hidden; }
.hc-scene{ position:absolute; inset:0; padding:14px; display:flex; flex-direction:column; gap:9px;
  opacity:0; transform:translateY(8px); transition:opacity .5s ease, transform .5s ease; pointer-events:none; overflow:hidden; }
.hc-scene.hc-on{ opacity:1; transform:none; }
.hc-pagettl{ font-size:1.02rem; font-weight:750; }
.hc-muted{ color:var(--muted); }
.hc-fs12{ font-size:.74rem; } .hc-fs11{ font-size:.68rem; } .hc-fw{ font-weight:650; }
.hc-card{ background:var(--surface); border:1px solid var(--border); border-radius:var(--r-md); padding:11px; }

.hc-welcome{ background:linear-gradient(120deg,var(--accent-soft),transparent); border:1px solid var(--border);
  border-radius:var(--r-md); padding:9px 12px; }
.hc-welcome .hc-dt{ font-size:.62rem; color:var(--muted); }
.hc-welcome h3{ font-size:.9rem; font-weight:750; margin:1px 0 2px; }
.hc-welcome p{ font-size:.66rem; color:var(--muted); }
.hc-sumgrid{ display:grid; grid-template-columns:repeat(5,1fr); gap:7px; }
@media(max-width:600px){ .hc-sumgrid{ grid-template-columns:repeat(2,1fr); } }
.hc-sum{ background:var(--surface); border:1px solid var(--border); border-radius:var(--r-sm); padding:8px; }
.hc-sum .hc-v{ font-size:1.05rem; font-weight:800; font-variant-numeric:tabular-nums; line-height:1.1; }
.hc-sum .hc-t{ font-size:.58rem; color:var(--muted); margin-top:2px; }
.hc-dcols{ display:grid; grid-template-columns:1.2fr .8fr; gap:9px; flex:1; min-height:0; }
@media(max-width:600px){ .hc-dcols{ grid-template-columns:1fr; } }
.hc-dsec{ background:var(--surface); border:1px solid var(--border); border-radius:var(--r-sm); padding:9px 10px; min-width:0; }
.hc-dsec .hc-h{ display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }
.hc-dsec .hc-h b{ font-size:.76rem; }
.hc-dsec .hc-h a{ font-size:.62rem; color:var(--accent); text-decoration:none; }
.hc-prow{ display:flex; justify-content:space-between; align-items:center; gap:8px; padding:6px 8px;
  border:1px solid var(--border); border-radius:var(--r-sm); margin-bottom:5px; }
.hc-prow strong{ font-size:.72rem; font-weight:650; }
.hc-prow small{ display:block; font-size:.58rem; color:var(--muted); }
.hc-prow .hc-cnt{ font-size:.72rem; font-weight:700; color:var(--accent); }
.hc-act{ display:flex; justify-content:space-between; gap:8px; font-size:.66rem; padding:5px 2px; border-bottom:1px solid var(--border); }
.hc-act:last-child{ border-bottom:none; }
.hc-act .hc-tm{ color:var(--muted); white-space:nowrap; font-variant-numeric:tabular-nums; }
.hc-qa{ display:flex; gap:7px; flex-wrap:wrap; }
.hc-qa a{ font-size:.68rem; font-weight:600; color:var(--accent); background:var(--accent-soft);
  border-radius:var(--r-sm); padding:7px 11px; text-decoration:none; border:1px solid var(--border); }

.hc-steps-row{ display:flex; gap:6px; flex-wrap:wrap; }
.hc-stp{ font-size:.66rem; padding:5px 9px; border-radius:999px; border:1px solid var(--border); color:var(--muted); white-space:nowrap; }
.hc-stp.hc-done{ color:var(--success-text); border-color:var(--success-border); background:var(--success-bg); }
.hc-stp.hc-cur{ color:var(--accent); border-color:var(--accent); background:var(--accent-soft); font-weight:650; }
.hc-wfield{ display:flex; flex-direction:column; gap:4px; }
.hc-wfield label{ font-size:.72rem; font-weight:650; }
.hc-inp{ border:1px solid var(--border); border-radius:var(--r-sm); padding:8px 10px; background:var(--surface); font-size:.74rem; color:var(--text); }
.hc-inp.hc-sel{ display:flex; justify-content:space-between; align-items:center; color:var(--muted); }
.hc-inp.hc-ph{ color:var(--muted); }
.hc-inp.hc-focus{ border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft); }
.hc-radios{ display:flex; flex-direction:column; gap:6px; }
.hc-radio{ display:flex; gap:9px; align-items:flex-start; border:1px solid var(--border); border-radius:var(--r-sm); padding:8px 10px; }
.hc-radio.hc-sel{ border-color:var(--accent); background:var(--accent-soft); }
.hc-radio .hc-rr{ width:14px; height:14px; border-radius:50%; border:2px solid var(--border); flex:none; margin-top:2px; }
.hc-radio.hc-sel .hc-rr{ border-color:var(--accent); background:radial-gradient(circle 4px at 50% 50%, var(--accent) 98%, transparent); }
.hc-radio .hc-rt{ font-size:.72rem; font-weight:600; }
.hc-radio .hc-rp{ font-size:.6rem; color:var(--success-text); }

.hc-modgrid{ display:grid; grid-template-columns:repeat(2,1fr); gap:8px; overflow:hidden; }
@media(max-width:600px){ .hc-modgrid{ grid-template-columns:1fr; } }
.hc-modc{ background:var(--surface); border:1px solid var(--border); border-radius:var(--r-sm); padding:8px 10px; display:flex; flex-direction:column; gap:5px; }
.hc-modc .hc-mtop{ display:flex; align-items:center; justify-content:space-between; gap:8px; }
.hc-modc .hc-nm{ font-size:.74rem; font-weight:650; }
.hc-modc .hc-cost{ font-size:.64rem; font-weight:700; color:var(--accent); white-space:nowrap; }
.hc-modc .hc-cost.hc-free{ color:var(--success-text); }
.hc-modph{ display:flex; flex-direction:column; gap:3px; }
.hc-modph i{ height:4px; border-radius:2px; background:var(--border); opacity:.75; }
.hc-modph i:nth-child(2){ width:72%; }
.hc-tag{ align-self:flex-start; font-size:.56rem; color:var(--muted); border:1px solid var(--border); border-radius:999px; padding:2px 7px; }
.hc-tag.hc-synth{ color:var(--hot); border-color:color-mix(in srgb,var(--hot) 40%,var(--border)); }

.hc-run{ flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:10px; position:relative; text-align:center; }
.hc-ring{ width:62px; height:62px; border-radius:50%; background:conic-gradient(var(--accent) 74%, var(--border) 0);
  -webkit-mask:radial-gradient(circle 21px at 50% 50%, transparent 98%, #000 100%);
  mask:radial-gradient(circle 21px at 50% 50%, transparent 98%, #000 100%); animation:hc-spin 3s linear infinite; }
@keyframes hc-spin{ to{ transform:rotate(360deg); } }
.hc-rtext{ font-weight:700; font-size:.88rem; }
.hc-rtick{ font-family:var(--mono); font-size:.76rem; color:var(--muted); font-variant-numeric:tabular-nums; }
.hc-det{ font-size:.66rem; color:var(--success-text); background:var(--success-bg); border:1px solid var(--success-border); border-radius:999px; padding:4px 11px; }
.hc-pnote{ font-size:.6rem; color:var(--muted); max-width:44ch; line-height:1.5; }
.hc-spark{ position:absolute; inset:0; overflow:hidden; pointer-events:none; opacity:.5; }
.hc-spark i{ position:absolute; width:5px; height:5px; border-radius:50%; background:var(--accent); opacity:.5; animation:hc-float 4s ease-in-out infinite; }
@keyframes hc-float{ 0%,100%{ transform:translateY(0); } 50%{ transform:translateY(-14px); } }

.hc-tiles{ display:flex; gap:8px; }
.hc-stat{ flex:1; background:var(--surface); border:1px solid var(--border); border-radius:var(--r-sm); padding:8px 10px; }
.hc-stat .hc-n{ font-weight:800; font-size:1.05rem; font-variant-numeric:tabular-nums; }
.hc-stat .hc-k{ font-size:.6rem; color:var(--muted); }
.hc-rep{ display:grid; grid-template-columns:1.15fr .85fr; gap:10px; flex:1; min-height:0; }
@media(max-width:600px){ .hc-rep{ grid-template-columns:1fr; } }
.hc-site{ position:relative; background:var(--surface); border:1px solid var(--border); border-radius:var(--r-sm); overflow:hidden; min-height:120px; }
.hc-site .hc-nav{ height:15px; background:var(--accent-soft); }
.hc-site .hc-heroblk{ height:32px; margin:9px; border-radius:5px; background:var(--border); }
.hc-site .hc-lines{ margin:0 9px; display:flex; flex-direction:column; gap:5px; }
.hc-site .hc-lines i{ height:6px; border-radius:3px; background:var(--border); }
.hc-site .hc-lines i:nth-child(2){ width:80%; } .hc-site .hc-lines i:nth-child(3){ width:60%; }
.hc-site .hc-sctacta{ position:absolute; left:11px; bottom:11px; font-size:.58rem; font-weight:700; color:#fff; background:var(--accent); border-radius:4px; padding:5px 9px; }
.hc-site .hc-ctabox{ position:absolute; left:7px; bottom:7px; width:90px; height:28px; border:2px dashed var(--hot); border-radius:6px; }
.hc-site .hc-ctatag{ position:absolute; left:7px; bottom:38px; font-family:var(--mono); font-size:.54rem; color:var(--hot); font-weight:700; }
.hc-blob{ position:absolute; border-radius:50%; filter:blur(9px); pointer-events:none; mix-blend-mode:multiply; animation:hc-pulse 2.6s ease-in-out infinite; }
:root[data-theme="dark"] .hc-blob{ mix-blend-mode:screen; }
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]) .hc-blob{ mix-blend-mode:screen; } }
@keyframes hc-pulse{ 0%,100%{ opacity:.55; transform:scale(1); } 50%{ opacity:.85; transform:scale(1.08); } }
.hc-findings{ display:flex; flex-direction:column; gap:7px; min-width:0; }
.hc-find{ display:flex; gap:7px; font-size:.7rem; background:var(--surface); border:1px solid var(--border); border-radius:var(--r-sm); padding:8px 10px; }
.hc-find .hc-d{ width:7px; height:7px; border-radius:50%; flex:none; margin-top:5px; }
.hc-synthlabel{ font-size:.6rem; color:var(--warn-text); background:var(--warn-bg); border-radius:999px; padding:4px 9px; align-self:flex-start; font-weight:600; }

.hc-scrub{ padding:11px 14px; border-top:1px solid var(--border); background:var(--surface-2); display:flex; align-items:center; gap:10px; }
.hc-navbtn,.hc-playbtn{ width:32px; height:32px; border-radius:50%; border:1px solid var(--border); background:var(--surface); color:var(--text); cursor:pointer; flex:none; display:grid; place-items:center; font-size:.8rem; padding:0; }
.hc-navbtn:hover,.hc-playbtn:hover{ border-color:var(--accent); color:var(--accent); }
.hc-segs{ flex:1; display:flex; gap:6px; }
.hc-seg{ flex:1; cursor:pointer; padding:8px 0; }
.hc-seg .hc-track{ height:4px; border-radius:3px; background:var(--border); overflow:hidden; }
.hc-seg .hc-fill{ height:100%; width:0; background:var(--accent); border-radius:3px; }
.hc-caption{ font-size:.84rem; color:var(--muted); text-align:center; margin-top:12px; min-height:1.4em; }
.hc-caption b{ color:var(--text); }

.hc-steps{ padding:46px 0 8px; }
.hc-steps .hc-shead{ text-align:center; margin-bottom:24px; }
.hc-steps .hc-shead h2{ font-size:clamp(1.4rem,3vw,1.9rem); font-weight:750; margin-top:8px; }
.hc-grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; }
.hc-scard{ background:var(--surface); border:1px solid var(--border); border-radius:var(--r-md); padding:18px; box-shadow:var(--shadow); }
.hc-scard .hc-num{ font-family:var(--mono); font-size:.8rem; color:var(--accent); font-weight:700; }
.hc-scard h3{ font-size:1.02rem; font-weight:700; margin:8px 0 6px; }
.hc-scard p{ font-size:.9rem; color:var(--muted); }
.hc-integrity{ margin:30px 0 0; background:var(--surface); border:1px solid var(--border); border-radius:var(--r-md); padding:16px 18px; display:flex; gap:12px; align-items:flex-start; }
.hc-integrity .hc-bar{ width:5px; align-self:stretch; border-radius:3px; flex:none; background:linear-gradient(var(--accent),var(--hot-2)); }
.hc-integrity p{ font-size:.86rem; color:var(--muted); }
.hc-integrity b{ color:var(--text); }
.hc-cta{ text-align:center; padding:40px 0; }
.hc-cta h2{ font-size:clamp(1.3rem,3vw,1.8rem); font-weight:750; }
.hc-cta .hc-row{ margin-top:18px; display:flex; justify-content:center; }
.hc-cta a{ text-decoration:none; font-weight:650; font-size:.95rem; border-radius:var(--r-sm); padding:12px 24px; background:var(--accent); color:var(--accent-contrast); }
.hc-cta a:hover{ background:var(--accent-hover); }
.hc-cta .hc-note{ margin-top:12px; font-size:.78rem; color:var(--muted); }
.hc-footer{ text-align:center; padding:26px 0 44px; color:var(--muted); font-size:.8rem; border-top:1px solid var(--border); }

@media(prefers-reduced-motion:reduce){
  .hc-scene{ transition:none; } .hc-ring,.hc-blob,.hc-spark i{ animation:none; }
}
`;

interface Scene {
  nav: string;
  title: string;
  addr: string;
  cap: string;
}

const SCENES: Scene[] = [
  {
    nav: "genel",
    title: "Genel Bakış",
    addr: "app.synthetix-ux / genel-bakis",
    cap: "<b>Genel Bakış.</b> Özet kartları, projelerim ve testler, son aktiviteler ve hızlı işlemler tek ekranda.",
  },
  {
    nav: "",
    title: "Yeni test",
    addr: "app.synthetix-ux / tests / new",
    cap: "<b>Yeni test — Test Detayları.</b> Proje, test adı, hedef görev ve ana test türünü seçin. Üstte 5 adım.",
  },
  {
    nav: "moduller",
    title: "Analiz Modülleri",
    addr: "app.synthetix-ux / analiz-modulleri",
    cap: "<b>Analiz Modülleri Kataloğu.</b> Temel test, erişilebilirlik, A/B, ağ/cihaz, CTA, dikkat, hızlı özet ve AI raporu.",
  },
  {
    nav: "simulasyonlar",
    title: "Simülasyon",
    addr: "app.synthetix-ux / simulasyonlar",
    cap: "<b>Sentetik simülasyon çalışıyor.</b> Personalar deterministik motorda değerlendirilir — aynı girdi, aynı sonuç.",
  },
  {
    nav: "raporlar",
    title: "Rapor",
    addr: "app.synthetix-ux / raporlar / rapor",
    cap: "<b>Raporunuzu inceleyin.</b> Skorlar, sentetik dikkat ısı haritası, CTA katmanı ve erişilebilirlik bulguları.",
  },
];

const NAV_ITEMS: { key: string; label: string }[] = [
  { key: "genel", label: "Genel Bakış" },
  { key: "projeler", label: "Projeler" },
  { key: "simulasyonlar", label: "Simülasyonlar" },
  { key: "raporlar", label: "Raporlar" },
  { key: "chip", label: "Chip Cüzdanı" },
  { key: "personalar", label: "Personalar" },
  { key: "moduller", label: "Analiz Modülleri" },
  { key: "ayarlar", label: "Ayarlar" },
  { key: "yardim", label: "Yardım" },
];

export default function NasilCalisiyor() {
  const [theme, setTheme] = useState<PublicTheme>(getPublicTheme);
  const [active, setActive] = useState(0);
  const [playing, setPlaying] = useState(
    () => !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const fillsRef = useRef<HTMLDivElement[]>([]);
  const stateRef = useRef({ active: 0, playing: true, acc: 0, last: 0 });

  useLayoutEffect(() => {
    window.scrollTo({ top: 0, left: 0 });
    const frame = window.requestAnimationFrame(() => window.scrollTo({ top: 0, left: 0 }));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    applyPublicTheme(theme);
  }, [theme]);

  // rAF tabanli sahne dizici. React state yerine ref uzerinden ilerler ki her
  // karede yeniden render tetiklenmesin; yalnizca sahne degisince state guncellenir.
  useEffect(() => {
    stateRef.current.active = active;
    stateRef.current.playing = playing;
  }, [active, playing]);

  useEffect(() => {
    const DUR = 4200;
    const N = SCENES.length;
    let raf = 0;
    const st = stateRef.current;

    function setFills() {
      fillsRef.current.forEach((f, k) => {
        if (!f) return;
        f.style.width = k < st.active ? "100%" : k === st.active ? `${(st.acc / DUR) * 100}%` : "0%";
      });
    }

    function frame(ts: number) {
      if (!st.last) st.last = ts;
      const dt = ts - st.last;
      st.last = ts;
      if (st.playing) {
        st.acc += dt;
        if (st.acc >= DUR) {
          st.acc = 0;
          const next = (st.active + 1) % N;
          st.active = next;
          setActive(next);
        }
        setFills();
      }
      raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  function goTo(i: number) {
    stateRef.current.acc = 0;
    stateRef.current.active = i;
    setActive(i);
  }

  const N = SCENES.length;
  const timecode = `00:${String(Math.floor((active * 4.2) % 60)).padStart(2, "0")}`;

  return (
    <div className="hc-page">
      <style>{CSS}</style>
      <div className="hc-wrap">
        <header className="hc-top">
          <Link to="/" className="hc-brand" aria-label="Synthetix UX ana sayfa">
            <BrandLogo />
            <span className="hc-sep">· Nasıl çalışır?</span>
          </Link>
          <div className="hc-theme" role="group" aria-label="Görünüm tercihi">
            <button
              type="button"
              aria-label="Açık tema"
              title="Açık tema"
              aria-pressed={theme === "light"}
              onClick={() => setTheme("light")}
            >
              <span aria-hidden="true">☀</span>
            </button>
            <button
              type="button"
              aria-label="Koyu tema"
              title="Koyu tema"
              aria-pressed={theme === "dark"}
              onClick={() => setTheme("dark")}
            >
              <span aria-hidden="true">☾</span>
            </button>
          </div>
        </header>

        <section className="hc-hero">
          <p className="hc-eyebrow">Web siteleri ve dijital ürün ekipleri için</p>
          <h1>Synthetix UX'i içeriden görün</h1>
          <p className="hc-sub">
            Genel bakıştan test oluşturmaya, analiz modüllerinden paylaşılabilir rapora — uygulamanın
            içinde kısa bir tur.
          </p>
        </section>

        <section className="hc-player">
          <div className="hc-frame">
            <div className="hc-chrome">
              <div className="hc-dots" aria-hidden="true">
                <i></i>
                <i></i>
                <i></i>
              </div>
              <div className="hc-addr">{SCENES[active].addr}</div>
              <div className="hc-tc">{timecode}</div>
            </div>

            <div className="hc-stage">
              <div className="hc-app">
                <aside className="hc-side" aria-hidden="true">
                  <div className="hc-ws">Çalışma Alanı</div>
                  {NAV_ITEMS.slice(0, 5).map((n) => (
                    <a
                      key={n.key}
                      className={SCENES[active].nav === n.key ? "hc-active" : undefined}
                    >
                      {n.label}
                    </a>
                  ))}
                  <div className="hc-grp">Araçlar</div>
                  {NAV_ITEMS.slice(5, 7).map((n) => (
                    <a
                      key={n.key}
                      className={SCENES[active].nav === n.key ? "hc-active" : undefined}
                    >
                      {n.label}
                    </a>
                  ))}
                  <div className="hc-spacer"></div>
                  {NAV_ITEMS.slice(7).map((n) => (
                    <a key={n.key}>{n.label}</a>
                  ))}
                </aside>

                <div className="hc-main">
                  <div className="hc-topbar">
                    <span className="hc-ttl">{SCENES[active].title}</span>
                    <div className="hc-tr">
                      <span className="hc-newbtn">+ Yeni test</span>
                      <span className="hc-ava" aria-hidden="true"></span>
                    </div>
                  </div>

                  <div className="hc-content">
                    {/* Sahne 1: Genel Bakis */}
                    <div className={`hc-scene${active === 0 ? " hc-on" : ""}`}>
                      <div className="hc-welcome">
                        <div className="hc-dt">11 Ağustos 2026</div>
                        <h3>Hoş geldiniz, Demo Kullanıcı</h3>
                        <p>Synthetix UX Canlı Demo adına proje, test ve Chip kullanımına dair güncel özet.</p>
                      </div>
                      <div className="hc-sumgrid">
                        <div className="hc-sum"><div className="hc-v">500</div><div className="hc-t">Chip Bakiyesi</div></div>
                        <div className="hc-sum"><div className="hc-v">2</div><div className="hc-t">Analiz Modülleri</div></div>
                        <div className="hc-sum"><div className="hc-v">1</div><div className="hc-t">Toplam Proje</div></div>
                        <div className="hc-sum"><div className="hc-v">0</div><div className="hc-t">Devam Eden Çalışmalar</div></div>
                        <div className="hc-sum"><div className="hc-v">1</div><div className="hc-t">Tamamlanan Testler</div></div>
                      </div>
                      <div className="hc-dcols">
                        <div className="hc-dsec">
                          <div className="hc-h"><b>Projelerim ve Testler</b><a>Tüm projeler</a></div>
                          <div className="hc-prow"><span><strong>Demo Projesi</strong><small>1 test · 0 taslak · 1 tamamlandı</small></span><span className="hc-cnt">1</span></div>
                        </div>
                        <div className="hc-dsec">
                          <div className="hc-h"><b>Son Aktiviteler</b></div>
                          <div className="hc-act"><span>Rapor oluşturuldu: Demo raporu</span><span className="hc-tm">14:20</span></div>
                          <div className="hc-act"><span>Simülasyon tamamlandı</span><span className="hc-tm">14:18</span></div>
                        </div>
                      </div>
                      <div className="hc-fs12 hc-fw">Hızlı İşlemler</div>
                      <div className="hc-qa">
                        <a>Yeni test oluştur</a>
                        <a>Projeleri görüntüle</a>
                        <a>Chip kullanımını görüntüle</a>
                      </div>
                    </div>

                    {/* Sahne 2: Test Detaylari */}
                    <div className={`hc-scene${active === 1 ? " hc-on" : ""}`}>
                      <div className="hc-steps-row">
                        <span className="hc-stp hc-cur">1 · Test Detayları</span>
                        <span className="hc-stp">2 · Tasarım Kaynağı</span>
                        <span className="hc-stp">3 · Persona</span>
                        <span className="hc-stp">4 · Analiz Modülleri</span>
                        <span className="hc-stp">5 · Özet ve Başlat</span>
                      </div>
                      <div className="hc-card" style={{ display: "flex", flexDirection: "column", gap: "9px" }}>
                        <div className="hc-wfield"><label>Proje</label><div className="hc-inp hc-sel">Demo Projesi <span>▾</span></div></div>
                        <div className="hc-wfield"><label>Test adı</label><div className="hc-inp hc-focus">Landing kullanılabilirlik</div></div>
                        <div className="hc-wfield"><label>Hedef görev</label><div className="hc-inp hc-ph">Örn. Ziyaretçinin ücretsiz hesap oluşturması</div></div>
                        <div className="hc-wfield">
                          <label>Ana test türü</label>
                          <div className="hc-radios">
                            <div className="hc-radio hc-sel"><span className="hc-rr"></span><span><span className="hc-rt">Temel UX testi</span> · <span className="hc-rp">1 ücretsiz kullanım hakkı mevcut</span></span></div>
                            <div className="hc-radio"><span className="hc-rr"></span><span className="hc-rt">Erişilebilirlik ön kontrolü</span></div>
                            <div className="hc-radio"><span className="hc-rr"></span><span className="hc-rt">A/B tasarım karşılaştırması</span></div>
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* Sahne 3: Analiz Modulleri */}
                    <div className={`hc-scene${active === 2 ? " hc-on" : ""}`}>
                      <div className="hc-pagettl">Analiz Modülleri Kataloğu</div>
                      <div className="hc-modgrid">
                        <div className="hc-modc"><div className="hc-mtop"><span className="hc-nm">Temel UX testi</span><span className="hc-cost hc-free">Ücretsiz</span></div><div className="hc-modph"><i></i><i></i></div><span className="hc-tag hc-synth">Sentetik tahmin</span></div>
                        <div className="hc-modc"><div className="hc-mtop"><span className="hc-nm">Erişilebilirlik ön kontrolü</span><span className="hc-cost hc-free">Ücretsiz</span></div><div className="hc-modph"><i></i><i></i></div><span className="hc-tag">Teknik ölçüm</span></div>
                        <div className="hc-modc"><div className="hc-mtop"><span className="hc-nm">A/B tasarım karşılaştırması</span><span className="hc-cost hc-free">Ücretsiz</span></div><div className="hc-modph"><i></i><i></i></div><span className="hc-tag hc-synth">Sentetik tahmin</span></div>
                        <div className="hc-modc"><div className="hc-mtop"><span className="hc-nm">Ağ ve cihaz testi</span><span className="hc-cost">40 Chip</span></div><div className="hc-modph"><i></i><i></i></div><span className="hc-tag">Teknik ölçüm</span></div>
                        <div className="hc-modc"><div className="hc-mtop"><span className="hc-nm">Kampanya ve CTA testi</span><span className="hc-cost">35 Chip</span></div><div className="hc-modph"><i></i><i></i></div><span className="hc-tag hc-synth">Sentetik tahmin</span></div>
                        <div className="hc-modc"><div className="hc-mtop"><span className="hc-nm">Sentetik dikkat tahmini</span><span className="hc-cost">25 Chip</span></div><div className="hc-modph"><i></i><i></i></div><span className="hc-tag hc-synth">Sentetik tahmin</span></div>
                        <div className="hc-modc"><div className="hc-mtop"><span className="hc-nm">Hızlı rapor özeti</span><span className="hc-cost hc-free">Ücretsiz</span></div><div className="hc-modph"><i></i><i></i></div><span className="hc-tag hc-synth">Sentetik tahmin</span></div>
                        <div className="hc-modc"><div className="hc-mtop"><span className="hc-nm">AI raporu</span><span className="hc-cost">50 Chip</span></div><div className="hc-modph"><i></i><i></i></div><span className="hc-tag hc-synth">Sentetik tahmin</span></div>
                      </div>
                    </div>

                    {/* Sahne 4: Calisiyor */}
                    <div className={`hc-scene${active === 3 ? " hc-on" : ""}`}>
                      <div className="hc-run hc-card">
                        <div className="hc-spark" aria-hidden="true">
                          <i style={{ left: "18%", top: "30%" }}></i>
                          <i style={{ left: "70%", top: "24%", animationDelay: ".6s" }}></i>
                          <i style={{ left: "40%", top: "66%", animationDelay: "1.1s" }}></i>
                          <i style={{ left: "82%", top: "60%", animationDelay: "1.6s" }}></i>
                        </div>
                        <div className="hc-ring" aria-hidden="true"></div>
                        <div className="hc-rtext">Sentetik simülasyon çalışıyor…</div>
                        <div className="hc-rtick">100 persona · 6 metrik değerlendiriliyor</div>
                        <div className="hc-det">Deterministik · tekrarlanabilir</div>
                        <div className="hc-pnote">
                          Persona = sentetik profil arketipi; her biri daha büyük bir gerçek kitleyi temsil
                          eder (ör. 100 persona ≈ 50.000 kullanıcılık bir hedef kitle).
                        </div>
                      </div>
                    </div>

                    {/* Sahne 5: Rapor */}
                    <div className={`hc-scene${active === 4 ? " hc-on" : ""}`}>
                      <div className="hc-tiles">
                        <div className="hc-stat"><div className="hc-n">72</div><div className="hc-k">Kullanılabilirlik</div></div>
                        <div className="hc-stat"><div className="hc-n">84</div><div className="hc-k">Erişilebilirlik</div></div>
                        <div className="hc-stat"><div className="hc-n">67</div><div className="hc-k">Dikkat skoru</div></div>
                      </div>
                      <div className="hc-rep">
                        <div className="hc-site" aria-label="Sentetik dikkat ısı haritası ve CTA katmanı">
                          <div className="hc-nav"></div>
                          <div className="hc-heroblk"></div>
                          <div className="hc-lines"><i></i><i></i><i></i></div>
                          <div className="hc-ctatag" aria-hidden="true">birincil_cta</div>
                          <div className="hc-ctabox" aria-hidden="true"></div>
                          <div className="hc-sctacta" aria-hidden="true">Hemen Al</div>
                          <div className="hc-blob" style={{ left: "2%", top: 0, width: "60%", height: "30px", background: "var(--hot)" }}></div>
                          <div className="hc-blob" style={{ left: "22%", top: "18px", width: "66%", height: "48px", background: "var(--hot-2)", animationDelay: ".5s" }}></div>
                          <div className="hc-blob" style={{ left: 0, bottom: "2px", width: "44%", height: "40px", background: "var(--accent)", animationDelay: "1s" }}></div>
                        </div>
                        <div className="hc-findings">
                          <span className="hc-synthlabel">Gerçek kullanıcı verisi değildir · Sentetik dikkat tahmini</span>
                          <div className="hc-find"><span className="hc-d" style={{ background: "var(--hot)" }}></span><span><b>5 görselde</b> alt metin eksik.</span></div>
                          <div className="hc-find"><span className="hc-d" style={{ background: "var(--accent)" }}></span><span>Birincil <b>CTA sayfanın altında</b> — dikkat düşük.</span></div>
                          <div className="hc-find"><span className="hc-d" style={{ background: "var(--hot-2)" }}></span><span>Hero başlığı en yüksek dikkati alıyor.</span></div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="hc-scrub">
              <button className="hc-navbtn" type="button" aria-label="Önceki" onClick={() => goTo((active - 1 + N) % N)}>‹</button>
              <button
                className="hc-playbtn"
                type="button"
                aria-label={playing ? "Duraklat" : "Oynat"}
                onClick={() => setPlaying((p) => !p)}
              >
                {playing ? "❚❚" : "▶"}
              </button>
              <button className="hc-navbtn" type="button" aria-label="Sonraki" onClick={() => goTo((active + 1) % N)}>›</button>
              <div className="hc-segs" aria-hidden="true">
                {SCENES.map((_, i) => (
                  <div
                    key={i}
                    className="hc-seg"
                    role="button"
                    tabIndex={0}
                    aria-label={`${i + 1}. adıma git`}
                    onClick={() => goTo(i)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        goTo(i);
                      }
                    }}
                  >
                    <div className="hc-track">
                      <div
                        className="hc-fill"
                        ref={(el) => {
                          if (el) fillsRef.current[i] = el;
                        }}
                      ></div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <p className="hc-caption" dangerouslySetInnerHTML={{ __html: SCENES[active].cap }}></p>
        </section>

        <section className="hc-steps">
          <div className="hc-shead">
            <p className="hc-eyebrow">Daha düzenli bir araştırma akışı</p>
            <h2>İlk sorudan paylaşılabilir rapora kadar</h2>
          </div>
          <div className="hc-grid">
            <div className="hc-scard"><div className="hc-num">01</div><h3>Web sitenizi ekleyin</h3><p>URL veya ekran görüntüsüyle başlayın. Sayfa yalnızca pasif olarak, gizlilik öncelikli incelenir.</p></div>
            <div className="hc-scard"><div className="hc-num">02</div><h3>Hedef kitlenizi tanımlayın</h3><p>Sentetik persona arketiplerini yaş, cihaz ve uzmanlık dağılımıyla seçin; her persona daha geniş bir gerçek kitleyi temsil eder.</p></div>
            <div className="hc-scard"><div className="hc-num">03</div><h3>Modülleri seçin</h3><p>Temel UX testi ve erişilebilirlik ön kontrolü ücretsiz; dikkat, CTA ve AI raporu gibi gelişmiş modüller Chip ile.</p></div>
            <div className="hc-scard"><div className="hc-num">04</div><h3>Simülasyonu çalıştırın</h3><p>Deterministik motor arka planda çalışır — aynı girdi her zaman aynı, tekrarlanabilir sonucu verir.</p></div>
            <div className="hc-scard"><div className="hc-num">05</div><h3>Raporunuzu inceleyin</h3><p>Skorlar, dikkat ısı haritası, CTA katmanı ve erişilebilirlik bulguları tek, paylaşılabilir raporda.</p></div>
          </div>
          <div className="hc-integrity">
            <div className="hc-bar" aria-hidden="true"></div>
            <p>
              <b>Karar desteği için sentetik tahminler.</b> Sonuçlar gerçek kullanıcı araştırmasının
              yerini almaz; erken aşama riskleri ve araştırma önceliklerini belirlemenize yardımcı olur.
              Tüm çıktılar sentetik, tahmini ve kalibre edilmemiştir.
            </p>
          </div>
        </section>

        <section className="hc-cta">
          <h2>Kendi sitenizde deneyin</h2>
          <div className="hc-row">
            <Link to="/kayit">Ücretsiz hesap oluştur</Link>
          </div>
          <p className="hc-note">Kredi kartı gerekmez · 2 ücretsiz kullanım hakkı</p>
        </section>

        <footer className="hc-footer">© 2026 Synthetix UX · Sentetik UX test platformu</footer>
      </div>
    </div>
  );
}
