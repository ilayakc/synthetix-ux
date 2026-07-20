import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { type ReportListItemResponse, listReports } from "../api/client";

export default function Reports() {
  const [reports, setReports] = useState<ReportListItemResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listReports()
      .then(setReports)
      .catch(() => setError("Raporlar yüklenemedi."));
  }, []);

  return (
    <section aria-labelledby="reports-heading">
      <h1 id="reports-heading" className="page-heading">
        Raporlar
      </h1>
      <p className="page-placeholder">
        Tamamlanmış simülasyon çalıştırmalarından üretilen, değişmez sentetik senaryo raporları.
        Gerçek kullanıcı verisi değildir.
      </p>

      {error && <p className="page-placeholder">{error}</p>}

      {reports && reports.length === 0 && (
        <div className="empty-state">
          <p>Henüz tamamlanmış bir simülasyondan üretilmiş rapor yok.</p>
        </div>
      )}

      {reports && reports.length > 0 && (
        <div className="report-list">
          {reports.map((report) => (
            <Link key={report.id} to={`/raporlar/${report.id}`} className="report-list-item">
              <div className="report-list-item__meta">
                <span className="report-list-item__title">
                  {report.test_definition_name} — {report.variant_name}
                </span>
                <span className="report-list-item__sub">
                  {report.project_name} · {new Date(report.created_at).toLocaleString("tr-TR")}
                </span>
              </div>
              <span className="chip-pill">{report.calibration_status}</span>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
