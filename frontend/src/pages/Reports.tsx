import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  type ReportListItemResponse,
  type WizardDraftResponse,
  listReports,
  listWizardDrafts,
} from "../api/client";
import { calibrationStatusLabel } from "../lib/turkishCopy";

export default function Reports() {
  const [reports, setReports] = useState<ReportListItemResponse[] | null>(null);
  const [drafts, setDrafts] = useState<WizardDraftResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    return Promise.all([listReports(), listWizardDrafts()])
      .then(([reportItems, draftItems]) => {
        setReports(reportItems);
        setDrafts(draftItems.filter((draft) => draft.status === "draft"));
      })
      .catch(() => setError("Raporlar yüklenemedi."));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Bir A/B testi backend'de varyant başına ayrı, değişmez rapor üretir. Liste
  // ekranında ise bunlar kullanıcının gözünde tek testtir; aynı test tanımına
  // ait raporları tek kartta toplar, ayrıntıda iki varyantı birlikte gösteririz.
  const reportGroups = reports
    ? Array.from(
        reports.reduce((groups, report) => {
          const existing = groups.get(report.test_definition_id) ?? [];
          existing.push(report);
          groups.set(report.test_definition_id, existing);
          return groups;
        }, new Map<string, ReportListItemResponse[]>()),
      ).map(([, items]) => items)
    : null;

  return (
    <section aria-labelledby="reports-heading">
      <h1 id="reports-heading" className="page-heading">
        Raporlar
      </h1>
      <p className="page-placeholder">
        Tamamlanmış simülasyon çalıştırmalarından üretilen, değişmez sentetik senaryo raporları.
        Gerçek kullanıcı verisi değildir.
      </p>

      {error && (
        <div className="dashboard-error" role="alert">
          <p>{error}</p>
          <button type="button" className="btn-secondary" onClick={() => void load()}>
            Tekrar dene
          </button>
        </div>
      )}

      {reports && drafts && reports.length === 0 && drafts.length === 0 && (
        <div className="empty-state">
          <p>Henüz yarım kalan test veya tamamlanmış rapor yok.</p>
        </div>
      )}

      {drafts && drafts.length > 0 && (
        <section
          id="yarim-kalan-testler"
          className="reports-group"
          aria-labelledby="draft-reports-heading"
        >
          <div className="reports-group__heading">
            <div>
              <h2 id="draft-reports-heading">Yarım Kalan Testler</h2>
              <p>Kaldığınız adımdan devam ederek testi tamamlayabilirsiniz.</p>
            </div>
            <span className="chip-pill">{drafts.length}</span>
          </div>
          <div className="report-list">
            {drafts.map((draft) => (
              <Link key={draft.id} to={`/tests/new?draft=${draft.id}`} className="report-list-item">
                <div className="report-list-item__meta">
                  <span className="report-list-item__title">
                    {draft.payload.name?.trim() || "Adsız test taslağı"}
                  </span>
                  <span className="report-list-item__sub">
                    {draft.current_step}. adımda bırakıldı · Son değişiklik{" "}
                    {new Date(draft.updated_at).toLocaleString("tr-TR")}
                  </span>
                </div>
                <span className="report-list-item__action">Devam et →</span>
              </Link>
            ))}
          </div>
        </section>
      )}

      {reportGroups && reportGroups.length > 0 && (
        <section className="reports-group" aria-labelledby="completed-reports-heading">
          <div className="reports-group__heading">
            <div>
              <h2 id="completed-reports-heading">Tamamlanan Raporlar</h2>
              <p>Analizi tamamlanmış testlerin değişmez sonuçları.</p>
            </div>
            <span className="chip-pill">{reportGroups.length}</span>
          </div>
          <div className="report-list">
            {reportGroups.map((group) => {
              const report = group[0];
              const isComparison = group.length > 1;
              return (
                <Link
                  key={report.test_definition_id}
                  to={`/raporlar/${report.id}`}
                  className="report-list-item"
                >
                  <div className="report-list-item__meta">
                    <span className="report-list-item__title">{report.test_definition_name}</span>
                    <span className="report-list-item__sub">
                      {isComparison
                        ? "A/B karşılaştırması · 2 varyant tek raporda"
                        : report.variant_name}
                      {" · "}
                      {report.project_name} · {new Date(report.created_at).toLocaleString("tr-TR")}
                    </span>
                  </div>
                  <span className="chip-pill">
                    {isComparison ? "A/B" : calibrationStatusLabel(report.calibration_status)}
                  </span>
                </Link>
              );
            })}
          </div>
        </section>
      )}
    </section>
  );
}
