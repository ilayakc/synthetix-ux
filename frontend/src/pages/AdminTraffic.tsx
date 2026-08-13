import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  type AnalyticsOrganizationDetail,
  type AnalyticsOrganizationRow,
  type AnalyticsOverviewResponse,
  type AnalyticsUserRow,
  type AnalyticsVisitEvent,
  ApiError,
  type CreateTrackingLinkRequest,
  type TrackingLinkResponse,
  createTrackingLink,
  downloadAnalyticsUsersCsv,
  getAnalyticsOrganizationDetail,
  getAnalyticsOverview,
  listAnalyticsOrganizations,
  listAnalyticsUsers,
  listAnalyticsVisits,
  listTrackingLinks,
  updateTrackingLink,
} from "../api/client";

// "Girişler ve Trafik" yönetici ekranı. Filtre/sekme durumu URL query
// parametrelerinde tutulur (bkz. useSearchParams) — böylece paylaşılabilir/
// yenilenebilir. Tüm veri yalnızca platform-admin uçlarından gelir; sirket/
// kullanici adlari backend'de dogrulanmis join'lerden alinir.

const TABS = [
  { key: "overview", label: "Genel Bakış" },
  { key: "visits", label: "Ziyaretler" },
  { key: "users", label: "Kullanıcı Girişleri" },
  { key: "orgs", label: "Şirketler" },
  { key: "links", label: "Bağlantılar/Kampanyalar" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

const RANGE_PRESETS = [
  { key: "today", label: "Bugün" },
  { key: "7d", label: "Son 7 gün" },
  { key: "30d", label: "Son 30 gün" },
  { key: "custom", label: "Özel aralık" },
] as const;

const EVENT_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "Tüm olaylar" },
  { value: "page_view", label: "Sayfa görüntüleme" },
  { value: "visitor_session_started", label: "Ziyaretçi oturumu" },
  { value: "signup_completed", label: "Kayıt tamamlandı" },
  { value: "login_succeeded", label: "Başarılı giriş" },
  { value: "organization_created", label: "Şirket oluşturuldu" },
  { value: "first_test_started", label: "İlk test başlatıldı" },
  { value: "first_test_completed", label: "İlk test tamamlandı" },
  { value: "logout", label: "Çıkış" },
];

const EVENT_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  EVENT_TYPE_OPTIONS.filter((o) => o.value).map((o) => [o.value, o.label]),
);

const STATUS_LABELS: Record<string, string> = {
  active: "Aktif",
  admin: "Yönetici",
  demo: "Demo",
};

const PAGE_SIZE = 25;

function toISODate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function computeRange(
  preset: string,
  customStart: string,
  customEnd: string,
): { start?: string; end?: string } {
  const today = new Date();
  const end = toISODate(today);
  if (preset === "today") return { start: end, end };
  if (preset === "7d") {
    const s = new Date(today);
    s.setDate(s.getDate() - 6);
    return { start: toISODate(s), end };
  }
  if (preset === "custom") {
    return { start: customStart || undefined, end: customEnd || undefined };
  }
  // 30d (varsayılan)
  const s = new Date(today);
  s.setDate(s.getDate() - 29);
  return { start: toISODate(s), end };
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("tr-TR", { dateStyle: "short", timeStyle: "short" }).format(
    new Date(value),
  );
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("tr-TR", { dateStyle: "short" }).format(new Date(value));
}

function formatInt(value: number): string {
  return value.toLocaleString("tr-TR");
}

function formatPercent(rate: number): string {
  return `%${(rate * 100).toLocaleString("tr-TR", { maximumFractionDigits: 1 })}`;
}

export default function AdminTraffic() {
  const [searchParams, setSearchParams] = useSearchParams();

  const tab = (searchParams.get("tab") as TabKey) ?? "overview";
  const range = searchParams.get("range") ?? "30d";
  const customStart = searchParams.get("start") ?? "";
  const customEnd = searchParams.get("end") ?? "";
  const source = searchParams.get("source") ?? "";
  const campaign = searchParams.get("campaign") ?? "";

  const setParams = useCallback(
    (updates: Record<string, string | null>) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(updates)) {
            if (value === null || value === "") next.delete(key);
            else next.set(key, value);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const { start, end } = useMemo(
    () => computeRange(range, customStart, customEnd),
    [range, customStart, customEnd],
  );

  const filters = useMemo(
    () => ({ start, end, source: source || undefined, campaign: campaign || undefined }),
    [start, end, source, campaign],
  );

  return (
    <section className="admin-dashboard admin-traffic" aria-labelledby="traffic-heading">
      <header className="admin-dashboard__header">
        <div>
          <p className="admin-dashboard__eyebrow">Erişim ve büyüme</p>
          <h1 id="traffic-heading" className="page-heading">
            Girişler ve Trafik
          </h1>
          <p className="page-placeholder">
            Site trafiği, kayıtlar, kullanıcı girişleri, şirketler ve yönlendirme bağlantıları —
            yalnızca platform yöneticileri içindir.
          </p>
        </div>
        <span className="admin-dashboard__badge">Yönetici erişimi</span>
      </header>

      <div className="admin-traffic__filters" aria-label="Ortak filtreler">
        <div className="admin-traffic__ranges" role="group" aria-label="Tarih aralığı">
          {RANGE_PRESETS.map((preset) => (
            <button
              key={preset.key}
              type="button"
              className={`admin-traffic__range${range === preset.key ? " is-active" : ""}`}
              aria-pressed={range === preset.key}
              onClick={() => setParams({ range: preset.key })}
            >
              {preset.label}
            </button>
          ))}
        </div>
        {range === "custom" && (
          <div className="admin-traffic__custom-range">
            <label>
              <span>Başlangıç</span>
              <input
                type="date"
                value={customStart}
                onChange={(e) => setParams({ start: e.target.value })}
              />
            </label>
            <label>
              <span>Bitiş</span>
              <input
                type="date"
                value={customEnd}
                onChange={(e) => setParams({ end: e.target.value })}
              />
            </label>
          </div>
        )}
        <label className="admin-filter">
          <span>Trafik kaynağı</span>
          <input
            type="text"
            placeholder="ör. linkedin"
            value={source}
            onChange={(e) => setParams({ source: e.target.value })}
          />
        </label>
        <label className="admin-filter">
          <span>Kampanya</span>
          <input
            type="text"
            placeholder="ör. accelerator_august"
            value={campaign}
            onChange={(e) => setParams({ campaign: e.target.value })}
          />
        </label>
      </div>

      <nav className="admin-traffic__tabs" role="tablist" aria-label="Analitik sekmeleri">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`traffic-tab-${t.key}`}
            aria-selected={tab === t.key}
            aria-controls={`traffic-panel-${t.key}`}
            className={`admin-traffic__tab${tab === t.key ? " is-active" : ""}`}
            onClick={() => setParams({ tab: t.key })}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div
        id={`traffic-panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`traffic-tab-${tab}`}
        tabIndex={0}
      >
        {tab === "overview" && <OverviewTab filters={filters} />}
        {tab === "visits" && <VisitsTab filters={filters} />}
        {tab === "users" && <UsersTab />}
        {tab === "orgs" && <OrgsTab />}
        {tab === "links" && <LinksTab />}
      </div>
    </section>
  );
}

interface RangeFilters {
  start?: string;
  end?: string;
  source?: string;
  campaign?: string;
}

function useAsync<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    loader()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Veriler yüklenemedi.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, isLoading, error };
}

function ErrorNote({ message }: { message: string }) {
  return (
    <p className="auth-error" role="alert">
      {message}
    </p>
  );
}

function EmptyState({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="admin-empty-state">
      <strong>{title}</strong>
      <p>{hint}</p>
    </div>
  );
}

// --- Genel Bakış -----------------------------------------------------------

const METRIC_CARDS: Array<{
  key: keyof AnalyticsOverviewResponse["metrics"];
  label: string;
  description: string;
  kind?: "int" | "percent";
}> = [
  { key: "total_page_views", label: "Toplam sayfa görüntüleme", description: "Tüm zamanlar" },
  {
    key: "total_unique_visitors",
    label: "Toplam benzersiz ziyaretçi",
    description: "Tekil ziyaretçi",
  },
  { key: "unique_visitors_today", label: "Bugünkü benzersiz ziyaretçi", description: "Bugün" },
  { key: "unique_visitors_7d", label: "Son 7 gün benzersiz ziyaretçi", description: "7 gün" },
  { key: "unique_visitors_30d", label: "Son 30 gün benzersiz ziyaretçi", description: "30 gün" },
  { key: "total_users", label: "Toplam kayıtlı kullanıcı", description: "Tüm hesaplar" },
  {
    key: "total_organizations",
    label: "Toplam kayıtlı şirket",
    description: "Tüm çalışma alanları",
  },
  { key: "new_users_in_range", label: "Yeni kullanıcı", description: "Seçilen aralıkta" },
  { key: "new_organizations_in_range", label: "Yeni şirket", description: "Seçilen aralıkta" },
  { key: "successful_logins_in_range", label: "Başarılı giriş", description: "Seçilen aralıkta" },
  {
    key: "unique_login_users_in_range",
    label: "Tekil giriş yapan kullanıcı",
    description: "Seçilen aralıkta",
  },
  {
    key: "visitor_to_signup_rate",
    label: "Ziyaretçi → kayıt dönüşümü",
    description: "Yeni kullanıcı / benzersiz ziyaretçi",
    kind: "percent",
  },
  {
    key: "signup_to_first_login_rate",
    label: "Kayıt → ilk giriş dönüşümü",
    description: "Giriş yapmış kullanıcı / toplam kullanıcı",
    kind: "percent",
  },
  {
    key: "campaign_referred_visitors",
    label: "Kampanyadan gelen ziyaretçi",
    description: "Link/kampanya kaynaklı",
  },
  {
    key: "campaign_referred_signups",
    label: "Kampanyadan gelen kayıt",
    description: "Link/kampanya kaynaklı",
  },
];

function OverviewTab({ filters }: { filters: RangeFilters }) {
  const { data, isLoading, error } = useAsync(
    () => getAnalyticsOverview(filters),
    [filters.start, filters.end, filters.source, filters.campaign],
  );

  if (error) return <ErrorNote message={error} />;
  if (isLoading && !data) return <p className="page-placeholder">Özet yükleniyor…</p>;
  if (!data) return null;

  const { metrics, timeseries, top_pages, top_sources, top_campaigns, funnel } = data;

  return (
    <>
      <div className="admin-summary-grid" aria-label="Özet metrikler">
        {METRIC_CARDS.map((card) => (
          <article className="admin-summary-card" key={card.key}>
            <span>{card.label}</span>
            <strong>
              {card.kind === "percent"
                ? formatPercent(metrics[card.key])
                : formatInt(metrics[card.key])}
            </strong>
            <p>{card.description}</p>
          </article>
        ))}
      </div>

      <div className="admin-traffic__charts">
        <BarChart title="Gün bazında ziyaret" points={timeseries} field="visits" />
        <BarChart title="Gün bazında kayıt" points={timeseries} field="signups" />
        <BarChart title="Gün bazında başarılı giriş" points={timeseries} field="logins" />
      </div>

      <div className="admin-traffic__columns">
        <section className="admin-panel" aria-labelledby="top-pages-heading">
          <div className="admin-panel__header">
            <h2 id="top-pages-heading">En çok ziyaret edilen sayfalar</h2>
          </div>
          <RankedList
            items={top_pages.map((p) => ({ label: p.label, value: p.visitors }))}
            emptyLabel="Henüz ziyaret verisi yok"
            valueSuffix="ziyaretçi"
          />
        </section>
        <section className="admin-panel" aria-labelledby="top-sources-heading">
          <div className="admin-panel__header">
            <h2 id="top-sources-heading">En iyi trafik kaynakları</h2>
          </div>
          <RankedList
            items={top_sources.map((s) => ({ label: s.label, value: s.visitors }))}
            emptyLabel="Henüz kaynak verisi yok"
            valueSuffix="ziyaretçi"
          />
        </section>
        <section className="admin-panel" aria-labelledby="top-campaigns-heading">
          <div className="admin-panel__header">
            <h2 id="top-campaigns-heading">En iyi kampanyalar</h2>
          </div>
          <RankedList
            items={top_campaigns.map((c) => ({
              label: c.campaign,
              value: c.visitors,
              secondary: `${formatInt(c.signups)} kayıt`,
            }))}
            emptyLabel="Henüz kampanya verisi yok"
            valueSuffix="ziyaretçi"
          />
        </section>
      </div>

      <section className="admin-panel" aria-labelledby="funnel-heading">
        <div className="admin-panel__header">
          <div>
            <h2 id="funnel-heading">Dönüşüm hunisi</h2>
            <p>Ziyaret → kayıt → şirket oluşturma → ilk test.</p>
          </div>
        </div>
        <Funnel funnel={funnel} />
      </section>
    </>
  );
}

function BarChart({
  title,
  points,
  field,
}: {
  title: string;
  points: AnalyticsOverviewResponse["timeseries"];
  field: "visits" | "signups" | "logins";
}) {
  const max = Math.max(1, ...points.map((p) => p[field]));
  const total = points.reduce((sum, p) => sum + p[field], 0);
  return (
    <figure className="admin-chart">
      <figcaption className="admin-chart__title">
        {title} <span>({formatInt(total)})</span>
      </figcaption>
      {points.length === 0 ? (
        <p className="page-placeholder">Veri yok</p>
      ) : (
        <div className="admin-chart__bars" role="img" aria-label={`${title}: toplam ${total}`}>
          {points.map((p) => (
            <span
              key={p.day}
              className="admin-chart__bar"
              style={{ height: `${Math.round((p[field] / max) * 100)}%` }}
              title={`${formatDate(p.day)}: ${formatInt(p[field])}`}
            />
          ))}
        </div>
      )}
    </figure>
  );
}

function RankedList({
  items,
  emptyLabel,
  valueSuffix,
}: {
  items: Array<{ label: string; value: number; secondary?: string }>;
  emptyLabel: string;
  valueSuffix: string;
}) {
  if (items.length === 0) return <p className="page-placeholder">{emptyLabel}</p>;
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="admin-ranked-list">
      {items.map((item, index) => (
        <li key={`${item.label}-${index}`}>
          <div className="admin-ranked-list__row">
            <span className="admin-ranked-list__label" title={item.label}>
              {item.label}
            </span>
            <span className="admin-ranked-list__value">
              {formatInt(item.value)} {valueSuffix}
              {item.secondary ? ` · ${item.secondary}` : ""}
            </span>
          </div>
          <span
            className="admin-ranked-list__bar"
            style={{ width: `${Math.round((item.value / max) * 100)}%` }}
            aria-hidden="true"
          />
        </li>
      ))}
    </ul>
  );
}

function Funnel({ funnel }: { funnel: AnalyticsOverviewResponse["funnel"] }) {
  const stages = [
    { label: "Ziyaretçi", value: funnel.visitors },
    { label: "Kayıt", value: funnel.signups },
    { label: "Şirket oluşturma", value: funnel.organizations },
    { label: "İlk test", value: funnel.first_tests },
  ];
  const max = Math.max(1, ...stages.map((s) => s.value));
  return (
    <ol className="admin-funnel">
      {stages.map((stage, index) => {
        const prev = index === 0 ? null : stages[index - 1].value;
        const rate = prev && prev > 0 ? stage.value / prev : null;
        return (
          <li key={stage.label} className="admin-funnel__stage">
            <div className="admin-funnel__meta">
              <strong>{stage.label}</strong>
              <span>
                {formatInt(stage.value)}
                {rate !== null ? ` · ${formatPercent(rate)}` : ""}
              </span>
            </div>
            <span
              className="admin-funnel__bar"
              style={{ width: `${Math.round((stage.value / max) * 100)}%` }}
              aria-hidden="true"
            />
          </li>
        );
      })}
    </ol>
  );
}

// --- Ziyaretler ------------------------------------------------------------

function VisitsTab({ filters }: { filters: RangeFilters }) {
  const [eventType, setEventType] = useState("");
  const [page, setPage] = useState(0);

  useEffect(() => {
    setPage(0);
  }, [eventType, filters.start, filters.end, filters.source, filters.campaign]);

  const { data, isLoading, error } = useAsync(
    () =>
      listAnalyticsVisits({
        ...filters,
        event_type: eventType || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    [filters.start, filters.end, filters.source, filters.campaign, eventType, page],
  );

  return (
    <section className="admin-panel" aria-labelledby="visits-heading">
      <div className="admin-panel__header">
        <div>
          <h2 id="visits-heading">Ziyaret ve olay akışı</h2>
          <p>
            En yeni olaylar üstte. Anonim ziyaretçiler yalnızca gizlilik dostu alanlarla listelenir.
          </p>
        </div>
        <label className="admin-filter">
          <span>Olay türü</span>
          <select value={eventType} onChange={(e) => setEventType(e.target.value)}>
            {EVENT_TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error ? (
        <ErrorNote message={error} />
      ) : isLoading && !data ? (
        <p className="page-placeholder">Ziyaretler yükleniyor…</p>
      ) : !data || data.events.length === 0 ? (
        <EmptyState
          title="Kayıt yok"
          hint="Seçilen filtrelerde gösterilecek ziyaret olayı bulunamadı."
        />
      ) : (
        <>
          <div className="admin-table-wrapper">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Zaman</th>
                  <th>Olay</th>
                  <th>Sayfa</th>
                  <th>Kaynak / Kampanya</th>
                  <th>Cihaz</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((event: AnalyticsVisitEvent) => (
                  <tr key={event.id}>
                    <td>{formatDateTime(event.occurred_at)}</td>
                    <td>{EVENT_TYPE_LABELS[event.event_type] ?? event.event_type}</td>
                    <td>{event.path ?? "—"}</td>
                    <td>
                      <span>{event.utm_source ?? event.referrer_domain ?? "doğrudan"}</span>
                      {event.utm_campaign && <span>{event.utm_campaign}</span>}
                      {event.referral_code && <span>ref: {event.referral_code}</span>}
                    </td>
                    <td>
                      {[event.device_category, event.browser_family, event.os_family]
                        .filter(Boolean)
                        .join(" · ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination total={data.total} page={page} onChange={setPage} />
        </>
      )}
    </section>
  );
}

function Pagination({
  total,
  page,
  onChange,
}: {
  total: number;
  page: number;
  onChange: (page: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return (
    <div className="admin-pagination">
      <button type="button" disabled={page <= 0} onClick={() => onChange(page - 1)}>
        Önceki
      </button>
      <span>
        Sayfa {page + 1} / {pageCount} · {formatInt(total)} kayıt
      </span>
      <button type="button" disabled={page + 1 >= pageCount} onClick={() => onChange(page + 1)}>
        Sonraki
      </button>
    </div>
  );
}

// --- Kullanıcı Girişleri ---------------------------------------------------

function UsersTab() {
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<"last_login" | "registered" | "total_logins">("last_login");
  const [page, setPage] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => window.clearTimeout(id);
  }, [search]);

  useEffect(() => {
    setPage(0);
  }, [debouncedSearch, status, sort]);

  const { data, isLoading, error } = useAsync(
    () =>
      listAnalyticsUsers({
        search: debouncedSearch || undefined,
        status: status || undefined,
        sort,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    [debouncedSearch, status, sort, page],
  );

  async function handleExport() {
    setExporting(true);
    setExportError(null);
    try {
      const blob = await downloadAnalyticsUsersCsv({
        search: debouncedSearch || undefined,
        status: status || undefined,
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "kullanici-giris-istatistikleri.csv";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err instanceof ApiError ? err.message : "CSV dışa aktarma başarısız.");
    } finally {
      setExporting(false);
    }
  }

  return (
    <section className="admin-panel" aria-labelledby="users-heading">
      <div className="admin-panel__header">
        <div>
          <h2 id="users-heading">Giriş yapan kullanıcılar</h2>
          <p>
            Şirket adları ve roller doğrulanmış üyelik kayıtlarından gelir. Hassas güvenlik verisi
            gösterilmez.
          </p>
        </div>
        <div className="admin-traffic__toolbar">
          <label className="admin-filter">
            <span>Ara</span>
            <input
              type="search"
              placeholder="Kullanıcı, e-posta veya şirket"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          <label className="admin-filter">
            <span>Durum</span>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Tümü</option>
              <option value="active">Aktif</option>
              <option value="admin">Yönetici</option>
              <option value="demo">Demo</option>
            </select>
          </label>
          <label className="admin-filter">
            <span>Sırala</span>
            <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
              <option value="last_login">Son giriş</option>
              <option value="registered">Kayıt tarihi</option>
              <option value="total_logins">Toplam giriş</option>
            </select>
          </label>
          <button
            type="button"
            className="admin-action"
            disabled={exporting}
            onClick={() => void handleExport()}
          >
            {exporting ? "Dışa aktarılıyor…" : "CSV dışa aktar"}
          </button>
        </div>
      </div>

      {exportError && <ErrorNote message={exportError} />}
      {error ? (
        <ErrorNote message={error} />
      ) : isLoading && !data ? (
        <p className="page-placeholder">Kullanıcılar yükleniyor…</p>
      ) : !data || data.users.length === 0 ? (
        <EmptyState
          title="Kullanıcı bulunamadı"
          hint="Arama veya filtreleri değiştirmeyi deneyin."
        />
      ) : (
        <>
          <div className="admin-table-wrapper">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Kullanıcı</th>
                  <th>Şirket / Rol</th>
                  <th>Kayıt</th>
                  <th>İlk giriş</th>
                  <th>Son giriş</th>
                  <th>Giriş (top / 7g / 30g)</th>
                  <th>Durum</th>
                  <th>Kaynak (ilk / son)</th>
                </tr>
              </thead>
              <tbody>
                {data.users.map((user: AnalyticsUserRow) => (
                  <tr key={user.user_id}>
                    <td>
                      <strong>{user.display_name ?? user.email}</strong>
                      {user.display_name && <span>{user.email}</span>}
                    </td>
                    <td>
                      <span>{user.organization_name ?? "—"}</span>
                      {user.role && <span>{user.role}</span>}
                    </td>
                    <td>{formatDate(user.registered_at)}</td>
                    <td>{formatDateTime(user.first_login_at)}</td>
                    <td>{formatDateTime(user.last_login_at)}</td>
                    <td>
                      {formatInt(user.total_logins)} / {formatInt(user.logins_7d)} /{" "}
                      {formatInt(user.logins_30d)}
                    </td>
                    <td>
                      <span className={`admin-status admin-status--${user.account_status}`}>
                        {STATUS_LABELS[user.account_status] ?? user.account_status}
                      </span>
                    </td>
                    <td>
                      <span>{user.first_campaign ?? user.first_source ?? "—"}</span>
                      {(user.last_campaign ?? user.last_source) && (
                        <span>{user.last_campaign ?? user.last_source}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination total={data.total} page={page} onChange={setPage} />
        </>
      )}
    </section>
  );
}

// --- Şirketler -------------------------------------------------------------

function OrgsTab() {
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [sort, setSort] = useState<"last_activity" | "created" | "members">("last_activity");
  const [page, setPage] = useState(0);
  const [detailId, setDetailId] = useState<string | null>(null);

  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => window.clearTimeout(id);
  }, [search]);

  useEffect(() => {
    setPage(0);
  }, [debouncedSearch, sort]);

  const { data, isLoading, error } = useAsync(
    () =>
      listAnalyticsOrganizations({
        search: debouncedSearch || undefined,
        sort,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    [debouncedSearch, sort, page],
  );

  return (
    <section className="admin-panel" aria-labelledby="orgs-heading">
      <div className="admin-panel__header">
        <div>
          <h2 id="orgs-heading">Şirketler</h2>
          <p>
            Şirket adına tıklayınca kullanıcıları ve etkinlik özeti açılır. Tenant içeriği
            gösterilmez.
          </p>
        </div>
        <div className="admin-traffic__toolbar">
          <label className="admin-filter">
            <span>Ara</span>
            <input
              type="search"
              placeholder="Şirket adı"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          <label className="admin-filter">
            <span>Sırala</span>
            <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
              <option value="last_activity">Son etkinlik</option>
              <option value="created">Oluşturulma</option>
              <option value="members">Üye sayısı</option>
            </select>
          </label>
        </div>
      </div>

      {error ? (
        <ErrorNote message={error} />
      ) : isLoading && !data ? (
        <p className="page-placeholder">Şirketler yükleniyor…</p>
      ) : !data || data.organizations.length === 0 ? (
        <EmptyState title="Şirket bulunamadı" hint="Arama terimini değiştirmeyi deneyin." />
      ) : (
        <>
          <div className="admin-table-wrapper">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Şirket</th>
                  <th>Oluşturulma</th>
                  <th>Üye / Aktif (30g)</th>
                  <th>Giriş</th>
                  <th>Proje / Test</th>
                  <th>Son etkinlik</th>
                  <th>Edinim (ilk / son)</th>
                </tr>
              </thead>
              <tbody>
                {data.organizations.map((org: AnalyticsOrganizationRow) => (
                  <tr key={org.organization_id}>
                    <td>
                      <button
                        type="button"
                        className="admin-linklike"
                        onClick={() => setDetailId(org.organization_id)}
                      >
                        {org.name}
                      </button>
                    </td>
                    <td>{formatDate(org.created_at)}</td>
                    <td>
                      {formatInt(org.member_count)} / {formatInt(org.active_users_30d)}
                    </td>
                    <td>{formatInt(org.total_logins)}</td>
                    <td>
                      {formatInt(org.project_count)} / {formatInt(org.completed_tests)}
                    </td>
                    <td>{formatDateTime(org.last_activity_at)}</td>
                    <td>
                      <span>{org.first_campaign ?? org.first_source ?? "—"}</span>
                      {(org.last_campaign ?? org.last_source) && (
                        <span>{org.last_campaign ?? org.last_source}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination total={data.total} page={page} onChange={setPage} />
        </>
      )}

      {detailId && <OrgDetailDrawer orgId={detailId} onClose={() => setDetailId(null)} />}
    </section>
  );
}

function OrgDetailDrawer({ orgId, onClose }: { orgId: string; onClose: () => void }) {
  const { data, isLoading, error } = useAsync<AnalyticsOrganizationDetail>(
    () => getAnalyticsOrganizationDetail(orgId),
    [orgId],
  );

  return (
    <div className="admin-drawer" role="dialog" aria-modal="true" aria-label="Şirket detayı">
      <button
        type="button"
        className="admin-drawer__backdrop"
        aria-label="Kapat"
        onClick={onClose}
      />
      <div className="admin-drawer__panel">
        <div className="admin-drawer__header">
          <h3>{data?.name ?? "Şirket detayı"}</h3>
          <button type="button" className="admin-drawer__close" onClick={onClose}>
            Kapat
          </button>
        </div>
        {error ? (
          <ErrorNote message={error} />
        ) : isLoading || !data ? (
          <p className="page-placeholder">Detay yükleniyor…</p>
        ) : (
          <>
            <dl className="admin-drawer__stats">
              <div>
                <dt>Üye</dt>
                <dd>{formatInt(data.member_count)}</dd>
              </div>
              <div>
                <dt>Proje</dt>
                <dd>{formatInt(data.project_count)}</dd>
              </div>
              <div>
                <dt>Tamamlanan test</dt>
                <dd>{formatInt(data.completed_tests)}</dd>
              </div>
              <div>
                <dt>Toplam giriş</dt>
                <dd>{formatInt(data.total_logins)}</dd>
              </div>
              <div>
                <dt>İlk edinim</dt>
                <dd>{data.first_campaign ?? data.first_source ?? "—"}</dd>
              </div>
              <div>
                <dt>Son edinim</dt>
                <dd>{data.last_campaign ?? data.last_source ?? "—"}</dd>
              </div>
            </dl>
            <h4>Kullanıcılar</h4>
            {data.members.length === 0 ? (
              <p className="page-placeholder">Üye yok.</p>
            ) : (
              <div className="admin-table-wrapper">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Kullanıcı</th>
                      <th>Rol</th>
                      <th>Son giriş</th>
                      <th>Toplam giriş</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.members.map((member) => (
                      <tr key={member.user_id}>
                        <td>
                          <strong>{member.display_name ?? member.email}</strong>
                          {member.display_name && <span>{member.email}</span>}
                        </td>
                        <td>{member.role}</td>
                        <td>{formatDateTime(member.last_login_at)}</td>
                        <td>{formatInt(member.total_logins)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// --- Bağlantılar / Kampanyalar ---------------------------------------------

const EMPTY_LINK_FORM: CreateTrackingLinkRequest = {
  name: "",
  destination_path: "/",
  utm_source: "",
  utm_medium: "",
  utm_campaign: "",
  utm_content: "",
  description: "",
  is_active: true,
};

function LinksTab() {
  const [links, setLinks] = useState<TrackingLinkResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<CreateTrackingLinkRequest>(EMPTY_LINK_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setLinks(await listTrackingLinks());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bağlantılar yüklenemedi.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setFormError(null);
    setNotice(null);
    try {
      const created = await createTrackingLink({
        ...form,
        utm_source: form.utm_source || null,
        utm_medium: form.utm_medium || null,
        utm_campaign: form.utm_campaign || null,
        utm_content: form.utm_content || null,
        description: form.description || null,
      });
      setNotice(`“${created.name}” bağlantısı oluşturuldu (ref: ${created.referral_code}).`);
      setForm(EMPTY_LINK_FORM);
      await load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Bağlantı oluşturulamadı.");
    } finally {
      setSubmitting(false);
    }
  }

  async function toggleActive(link: TrackingLinkResponse) {
    try {
      await updateTrackingLink(link.id, { is_active: !link.is_active });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bağlantı güncellenemedi.");
    }
  }

  return (
    <>
      <section className="admin-panel" aria-labelledby="link-form-heading">
        <div className="admin-panel__header">
          <div>
            <h2 id="link-form-heading">Yeni takip bağlantısı</h2>
            <p>
              Hedef yalnızca izin verilen dahili bir yol olabilir (ör. <code>/kayit</code>).
              Referral kodu güvenli biçimde rastgele üretilir.
            </p>
          </div>
        </div>
        {formError && <ErrorNote message={formError} />}
        {notice && (
          <p className="auth-notice" role="status">
            {notice}
          </p>
        )}
        <form className="admin-link-form" onSubmit={(e) => void handleCreate(e)}>
          <label>
            <span>Bağlantı adı *</span>
            <input
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label>
            <span>Hedef sayfa</span>
            <input
              value={form.destination_path ?? "/"}
              onChange={(e) => setForm({ ...form, destination_path: e.target.value })}
            />
          </label>
          <label>
            <span>Source</span>
            <input
              value={form.utm_source ?? ""}
              onChange={(e) => setForm({ ...form, utm_source: e.target.value })}
            />
          </label>
          <label>
            <span>Medium</span>
            <input
              value={form.utm_medium ?? ""}
              onChange={(e) => setForm({ ...form, utm_medium: e.target.value })}
            />
          </label>
          <label>
            <span>Campaign</span>
            <input
              value={form.utm_campaign ?? ""}
              onChange={(e) => setForm({ ...form, utm_campaign: e.target.value })}
            />
          </label>
          <label>
            <span>Content</span>
            <input
              value={form.utm_content ?? ""}
              onChange={(e) => setForm({ ...form, utm_content: e.target.value })}
            />
          </label>
          <label className="admin-link-form__wide">
            <span>Açıklama</span>
            <input
              value={form.description ?? ""}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </label>
          <div className="admin-link-form__actions">
            <button
              type="submit"
              className="admin-action admin-action--approve"
              disabled={submitting}
            >
              {submitting ? "Oluşturuluyor…" : "Bağlantı oluştur"}
            </button>
          </div>
        </form>
      </section>

      <section className="admin-panel" aria-labelledby="link-list-heading">
        <div className="admin-panel__header">
          <h2 id="link-list-heading">Takip bağlantıları</h2>
        </div>
        {error ? (
          <ErrorNote message={error} />
        ) : isLoading ? (
          <p className="page-placeholder">Bağlantılar yükleniyor…</p>
        ) : links.length === 0 ? (
          <EmptyState
            title="Henüz bağlantı yok"
            hint="Yukarıdaki formla ilk takip bağlantınızı oluşturun."
          />
        ) : (
          <div className="admin-table-wrapper">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Bağlantı</th>
                  <th>Hedef / Kampanya</th>
                  <th>Ziyaret / Tekil</th>
                  <th>Kayıt / Şirket / İlk test</th>
                  <th>Dönüşüm</th>
                  <th>İlk / Son ziyaret</th>
                  <th>Durum</th>
                </tr>
              </thead>
              <tbody>
                {links.map((link) => (
                  <tr key={link.id}>
                    <td>
                      <strong>{link.name}</strong>
                      <span title={link.tracking_url}>{link.tracking_url}</span>
                      <span>ref: {link.referral_code}</span>
                    </td>
                    <td>
                      <span>{link.destination_path}</span>
                      {link.utm_campaign && <span>{link.utm_campaign}</span>}
                    </td>
                    <td>
                      {formatInt(link.stats.total_visits)} / {formatInt(link.stats.unique_visitors)}
                    </td>
                    <td>
                      {formatInt(link.stats.signups)} / {formatInt(link.stats.organizations)} /{" "}
                      {formatInt(link.stats.first_tests)}
                    </td>
                    <td>{formatPercent(link.stats.conversion_rate)}</td>
                    <td>
                      <span>{formatDate(link.stats.first_visit_at)}</span>
                      <span>{formatDate(link.stats.last_visit_at)}</span>
                    </td>
                    <td>
                      <button
                        type="button"
                        className={`admin-status admin-status--${link.is_active ? "approved" : "rejected"} admin-linklike`}
                        onClick={() => void toggleActive(link)}
                      >
                        {link.is_active ? "Aktif" : "Pasif"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
