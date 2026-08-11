import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { applyPublicTheme, getPublicTheme, type PublicTheme } from "../theme/publicTheme";
import { useAuth } from "../auth/AuthContext";
import BrandLogo from "../components/BrandLogo";
import SimulationBackdrop from "./landing/SimulationBackdrop";

// "Nasil calisir?" baglantisi, ayri olarak hazirlanan tanitim sayfasini acar.
// NOT: Su an bu bir on-izleme sayfasidir; ileride uygulama ici bir rota
// (or. /nasil-calisiyor) haline getirilebilir.
const HOW_IT_WORKS_URL = "https://claude.ai/code/artifact/073b8d6b-a02a-4b91-95bc-054baea2bdd8";

const FEATURES = [
  {
    title: "Dakikalar içinde başlayın",
    description:
      "Web sitenizi veya tasarımınızı ekleyin, hedef kitlenizi belirleyin ve testi başlatın.",
  },
  {
    title: "Riskleri erken görün",
    description:
      "Kullanılabilirlik, erişilebilirlik ve karşılaştırmalı analiz bulgularını tek yerde inceleyin.",
  },
  {
    title: "Ekibinizle paylaşın",
    description:
      "Bulguları düzenli raporlara dönüştürerek ürün kararlarını daha görünür hale getirin.",
  },
];

export default function Landing() {
  const [theme, setTheme] = useState<PublicTheme>(getPublicTheme);
  const { demoLogin } = useAuth();
  const navigate = useNavigate();
  const [demoLoading, setDemoLoading] = useState(false);
  const [demoError, setDemoError] = useState<string | null>(null);

  useEffect(() => {
    applyPublicTheme(theme);
  }, [theme]);

  async function handleDemo() {
    setDemoError(null);
    setDemoLoading(true);
    try {
      await demoLogin();
      // Giris basarili olunca oturum durumu "authenticated" olur ve "/"
      // rotasi Genel Bakis'i gosterir.
      navigate("/", { replace: true });
    } catch {
      setDemoError("Canlı demo şu anda kullanılamıyor. Lütfen daha sonra tekrar deneyin.");
    } finally {
      setDemoLoading(false);
    }
  }

  return (
    <div className="landing-page">
      <header className="landing-header">
        <Link to="/" className="landing-brand" aria-label="Synthetix UX ana sayfa">
          <BrandLogo />
        </Link>
        <div className="landing-theme-switch" role="group" aria-label="Görünüm tercihi">
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

      <main>
        <section className="landing-hero" aria-labelledby="landing-heading">
          <SimulationBackdrop />
          <div className="landing-hero__content">
            <p className="landing-eyebrow">Web siteleri ve dijital ürün ekipleri için</p>
            <h1 id="landing-heading">Tasarım risklerini geliştirmeye geçmeden önce görün.</h1>
            <p className="landing-hero__description">
              Web sitenizi sentetik persona senaryolarıyla inceleyin; kullanılabilirlik sorunlarını,
              erişilebilirlik sinyallerini ve iyileştirme fırsatlarını tek raporda toplayın.
            </p>
            <div className="landing-hero__actions">
              <Link to="/kayit" className="landing-primary-button landing-primary-button--large">
                Ücretsiz hesap oluştur
              </Link>
              <button
                type="button"
                className="landing-secondary-button"
                onClick={handleDemo}
                disabled={demoLoading}
                style={{ fontFamily: "inherit", cursor: demoLoading ? "default" : "pointer" }}
              >
                {demoLoading ? "Demo açılıyor…" : "Canlı demoyu incele"}
              </button>
              <Link to="/giris" className="landing-secondary-button">
                Zaten hesabım var
              </Link>
            </div>
            {demoError && (
              <p className="landing-hero__note" role="alert" style={{ color: "var(--color-danger-text)" }}>
                {demoError}
              </p>
            )}
            <p className="landing-hero__note">Kredi kartı gerekmez · 2 ücretsiz kullanım hakkı</p>
            <p className="landing-hero__howitworks">
              <a
                href={HOW_IT_WORKS_URL}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "var(--color-accent)", fontWeight: 600, textDecoration: "none" }}
              >
                Nasıl çalışır?
              </a>
            </p>
          </div>

          <div className="landing-preview" aria-label="Ürün akışı özeti">
            <div className="landing-preview__header">
              <span>Yeni analiz</span>
              <span className="status-badge status-badge--draft">3 adım</span>
            </div>
            <ol className="landing-preview__steps">
              <li>
                <span>1</span>
                <div>
                  <strong>Web sitenizi ekleyin</strong>
                  <p>URL veya ekran görüntüsüyle başlayın.</p>
                </div>
              </li>
              <li>
                <span>2</span>
                <div>
                  <strong>Hedef kitlenizi tanımlayın</strong>
                  <p>Persona ve cihaz dağılımını seçin.</p>
                </div>
              </li>
              <li>
                <span>3</span>
                <div>
                  <strong>Raporunuzu inceleyin</strong>
                  <p>Bulguları ve öncelikli önerileri görün.</p>
                </div>
              </li>
            </ol>
          </div>
        </section>

        <section className="landing-features" aria-labelledby="landing-features-heading">
          <div className="landing-section-heading">
            <p className="landing-eyebrow">Daha düzenli bir araştırma akışı</p>
            <h2 id="landing-features-heading">İlk sorudan paylaşılabilir rapora kadar</h2>
          </div>
          <div className="landing-feature-grid">
            {FEATURES.map((feature, index) => (
              <article key={feature.title} className="landing-feature-card">
                <span aria-hidden="true">0{index + 1}</span>
                <h3>{feature.title}</h3>
                <p>{feature.description}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="landing-integrity" aria-label="Bilimsel dürüstlük açıklaması">
          <strong>Karar desteği için sentetik tahminler</strong>
          <p>
            Sonuçlar gerçek kullanıcı araştırmasının yerini almaz; erken aşama riskleri ve araştırma
            önceliklerini belirlemenize yardımcı olur.
          </p>
        </section>
      </main>

      <footer className="landing-footer">
        <span>© 2026 Synthetix UX</span>
      </footer>
    </div>
  );
}
