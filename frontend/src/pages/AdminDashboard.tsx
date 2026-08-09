import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  type AdminSummaryResponse,
  type AdminTopUpRequestResponse,
  type TopUpRequestStatus,
  getAdminSummary,
  listAdminTopUpRequests,
  reviewAdminTopUpRequest,
} from "../api/client";

const STATUS_LABELS: Record<TopUpRequestStatus, string> = {
  pending: "Beklemede",
  approved: "Onaylandı",
  rejected: "Reddedildi",
};

const SUMMARY_ITEMS: Array<{
  key: keyof AdminSummaryResponse;
  label: string;
  description: string;
}> = [
  { key: "pending_topup_count", label: "Bekleyen Chip talebi", description: "İnceleme bekliyor" },
  { key: "organization_count", label: "Şirket", description: "Platformdaki çalışma alanları" },
  { key: "user_count", label: "Kullanıcı", description: "Kayıtlı hesaplar" },
  {
    key: "failed_ai_pipeline_count",
    label: "Başarısız AI işlemi",
    description: "Operasyonel inceleme gerekebilir",
  },
];

type AdminSection = "overview" | "topups" | "ai" | "audit";

const SECTION_COPY: Record<AdminSection, { eyebrow: string; title: string; description: string }> =
  {
    overview: {
      eyebrow: "Platform yönetimi",
      title: "Yönetim Özeti",
      description: "Platformun güncel operasyonel durumunu tek yerden izleyin.",
    },
    topups: {
      eyebrow: "Finansal operasyonlar",
      title: "Chip Talepleri",
      description: "Şirketlerin Chip yükleme taleplerini güvenli biçimde onaylayın veya reddedin.",
    },
    ai: {
      eyebrow: "Sistem sağlığı",
      title: "AI İşlemleri",
      description: "AI raporu işlem hattının durumunu ve müdahale gerektiren hataları izleyin.",
    },
    audit: {
      eyebrow: "Denetim",
      title: "İşlem Kayıtları",
      description: "Yöneticiler tarafından sonuçlandırılmış Chip taleplerini inceleyin.",
    },
  };

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("tr-TR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function AdminDashboard({ section }: { section: AdminSection }) {
  const [summary, setSummary] = useState<AdminSummaryResponse | null>(null);
  const [requests, setRequests] = useState<AdminTopUpRequestResponse[]>([]);
  const [filter, setFilter] = useState<"all" | TopUpRequestStatus>(
    section === "audit" ? "all" : "pending",
  );
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const sectionCopy = SECTION_COPY[section];

  const loadData = useCallback(async () => {
    setError(null);
    try {
      const [summaryResponse, requestResponse] = await Promise.all([
        getAdminSummary(),
        listAdminTopUpRequests(filter === "all" ? undefined : filter),
      ]);
      setSummary(summaryResponse);
      setRequests(requestResponse);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yönetim bilgileri yüklenemedi.");
    } finally {
      setIsLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    setFilter(section === "audit" ? "all" : "pending");
  }, [section]);

  useEffect(() => {
    setIsLoading(true);
    void loadData();
  }, [loadData]);

  const visibleRequests = useMemo(
    () =>
      section === "audit" ? requests.filter((request) => request.status !== "pending") : requests,
    [requests, section],
  );
  const pendingCount = useMemo(
    () => requests.filter((request) => request.status === "pending").length,
    [requests],
  );

  async function handleReview(request: AdminTopUpRequestResponse, action: "approve" | "reject") {
    const note = notes[request.id]?.trim();
    if (action === "reject" && !note) {
      setError("Bir talebi reddetmek için açıklama yazın.");
      return;
    }

    setProcessingId(request.id);
    setError(null);
    setNotice(null);
    try {
      await reviewAdminTopUpRequest(request.id, action, note);
      setNotice(
        action === "approve"
          ? `${request.organization_name} için ${request.chip_amount.toLocaleString("tr-TR")} Chip onaylandı.`
          : `${request.organization_name} talebi reddedildi.`,
      );
      setNotes((current) => ({ ...current, [request.id]: "" }));
      await loadData();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Talep işlenemedi.");
    } finally {
      setProcessingId(null);
    }
  }

  return (
    <section className="admin-dashboard" aria-labelledby="admin-heading">
      <header className="admin-dashboard__header">
        <div>
          <p className="admin-dashboard__eyebrow">{sectionCopy.eyebrow}</p>
          <h1 id="admin-heading" className="page-heading">
            {sectionCopy.title}
          </h1>
          <p className="page-placeholder">{sectionCopy.description}</p>
        </div>
        <span className="admin-dashboard__badge">Yönetici erişimi</span>
      </header>

      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="auth-notice" role="status">
          {notice}
        </p>
      )}

      {section === "overview" && (
        <>
          <div className="admin-summary-grid" aria-label="Platform özeti">
            {SUMMARY_ITEMS.map((item) => (
              <article className="admin-summary-card" key={item.key}>
                <span>{item.label}</span>
                <strong>{summary ? summary[item.key].toLocaleString("tr-TR") : "—"}</strong>
                <p>{item.description}</p>
              </article>
            ))}
          </div>
          <div className="admin-overview-links" aria-label="Yönetim bölümleri">
            <Link to="/yonetim/chip-talepleri">
              <strong>Chip taleplerini incele</strong>
              <span>{summary?.pending_topup_count ?? 0} talep karar bekliyor</span>
            </Link>
            <Link to="/yonetim/ai-islemleri">
              <strong>AI işlem durumunu görüntüle</strong>
              <span>{summary?.failed_ai_pipeline_count ?? 0} başarısız işlem</span>
            </Link>
            <Link to="/yonetim/islem-kayitlari">
              <strong>İşlem kayıtlarını aç</strong>
              <span>Onay ve red geçmişini inceleyin</span>
            </Link>
          </div>
        </>
      )}

      {section === "ai" && (
        <section className="admin-panel admin-ai-status" aria-labelledby="ai-status-heading">
          <div>
            <span className="admin-ai-status__indicator" aria-hidden="true" />
            <div>
              <h2 id="ai-status-heading">AI işlem hattı durumu</h2>
              <p>Başarısız veya manuel inceleme gerektiren rapor işlemleri.</p>
            </div>
          </div>
          <strong>{summary?.failed_ai_pipeline_count ?? "—"}</strong>
          <p>
            {summary?.failed_ai_pipeline_count
              ? "Başarısız işlemler worker ve provider loglarıyla incelenmelidir."
              : "Şu anda müdahale gerektiren başarısız AI işlemi bulunmuyor."}
          </p>
        </section>
      )}

      {(section === "topups" || section === "audit") && (
        <section className="admin-panel" aria-labelledby="topup-requests-heading">
          <div className="admin-panel__header">
            <div>
              <h2 id="topup-requests-heading">
                {section === "audit" ? "Sonuçlandırılmış talepler" : "Chip yükleme talepleri"}
              </h2>
              <p>
                {section === "audit"
                  ? "İşlemi yapan yönetici, karar zamanı ve açıklaması burada saklanır."
                  : "Onaylanan tutar Chip defterine yalnızca bir kez eklenir. Reddedilen talepler bakiye oluşturmaz."}
              </p>
            </div>
            {section === "topups" && (
              <label className="admin-filter">
                <span>Durum</span>
                <select
                  value={filter}
                  onChange={(event) => setFilter(event.target.value as typeof filter)}
                >
                  <option value="pending">Bekleyenler</option>
                  <option value="approved">Onaylananlar</option>
                  <option value="rejected">Reddedilenler</option>
                  <option value="all">Tümü</option>
                </select>
              </label>
            )}
          </div>

          {isLoading ? (
            <p className="page-placeholder">Talepler yükleniyor…</p>
          ) : visibleRequests.length === 0 ? (
            <div className="admin-empty-state">
              <strong>
                {section === "audit"
                  ? "Henüz sonuçlandırılmış talep yok"
                  : filter === "pending"
                    ? "Bekleyen talep yok"
                    : "Bu durumda talep yok"}
              </strong>
              <p>Yeni bir kayıt oluştuğunda burada görüntülenecek.</p>
            </div>
          ) : (
            <div className="admin-table-wrapper">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Şirket ve kullanıcı</th>
                    <th>Talep</th>
                    <th>Durum</th>
                    <th>Tarih</th>
                    <th>{section === "audit" ? "Karar kaydı" : "İşlem"}</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRequests.map((request) => (
                    <tr key={request.id}>
                      <td>
                        <strong>{request.organization_name}</strong>
                        <span>
                          {request.requested_by_display_name ?? request.requested_by_email}
                        </span>
                        {request.requested_by_display_name && (
                          <span>{request.requested_by_email}</span>
                        )}
                      </td>
                      <td>
                        <strong>{request.chip_amount.toLocaleString("tr-TR")} Chip</strong>
                        <span>{request.package_key}</span>
                      </td>
                      <td>
                        <span className={`admin-status admin-status--${request.status}`}>
                          {STATUS_LABELS[request.status]}
                        </span>
                      </td>
                      <td>
                        <span>{formatDate(request.created_at)}</span>
                        {request.reviewed_at && (
                          <span>İşlendi: {formatDate(request.reviewed_at)}</span>
                        )}
                      </td>
                      <td>
                        {section === "topups" && request.status === "pending" ? (
                          <div className="admin-review-controls">
                            <input
                              aria-label={`${request.organization_name} için yönetici notu`}
                              placeholder="Yönetici notu"
                              maxLength={500}
                              value={notes[request.id] ?? ""}
                              onChange={(event) =>
                                setNotes((current) => ({
                                  ...current,
                                  [request.id]: event.target.value,
                                }))
                              }
                            />
                            <div>
                              <button
                                type="button"
                                className="admin-action admin-action--approve"
                                disabled={processingId === request.id}
                                onClick={() => void handleReview(request, "approve")}
                              >
                                Onayla
                              </button>
                              <button
                                type="button"
                                className="admin-action admin-action--reject"
                                disabled={processingId === request.id}
                                onClick={() => void handleReview(request, "reject")}
                              >
                                Reddet
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div className="admin-review-result">
                            <span>{request.reviewed_by_email ?? "Yönetici"}</span>
                            <span>{request.review_note ?? "Not eklenmedi"}</span>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {!isLoading && section === "topups" && filter === "pending" && pendingCount > 0 && (
            <p className="admin-panel__footnote">{pendingCount} talep kararınızı bekliyor.</p>
          )}
        </section>
      )}
    </section>
  );
}
