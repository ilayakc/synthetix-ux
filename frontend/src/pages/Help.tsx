import { useMemo, useState, type ComponentType, type SVGProps } from "react";
import { Link } from "react-router-dom";
import {
  CheckCircleIcon,
  ChipCoinIcon,
  FileTextIcon,
  HelpCircleIcon,
  InfoIcon,
  LayersIcon,
  PlusIcon,
  SearchIcon,
} from "../components/icons";

type HelpIcon = ComponentType<SVGProps<SVGSVGElement>>;

interface QuickLink {
  title: string;
  description: string;
  to: string;
  action: string;
  icon: HelpIcon;
}

interface FaqItem {
  category: string;
  question: string;
  answer: string;
}

const QUICK_LINKS: QuickLink[] = [
  {
    title: "Yeni bir test oluşturun",
    description:
      "Test türünü, tasarım kaynağını, personaları ve analiz modüllerini adım adım seçin.",
    to: "/tests/new",
    action: "Teste başlayın",
    icon: PlusIcon,
  },
  {
    title: "Analizleri karşılaştırın",
    description: "Her modülün ne ürettiğini, ücretsiz hakkını ve güncel Chip bedelini inceleyin.",
    to: "/analiz-modulleri",
    action: "Modülleri inceleyin",
    icon: LayersIcon,
  },
  {
    title: "Raporlarınıza dönün",
    description:
      "Tamamlanan raporları açın veya yarım kalan testlerinize kaldığınız yerden devam edin.",
    to: "/raporlar",
    action: "Raporları görüntüleyin",
    icon: FileTextIcon,
  },
  {
    title: "Chip kullanımını izleyin",
    description:
      "Bakiyenizi, paketleri, ücretsiz hakları ve geçmiş Chip taleplerini tek yerde görün.",
    to: "/kullanim-ve-chip",
    action: "Cüzdanı açın",
    icon: ChipCoinIcon,
  },
];

const WORKFLOW_STEPS = [
  ["1", "Test türü", "Temel UX, erişilebilirlik veya aynı tasarımın A/B karşılaştırmasını seçin."],
  [
    "2",
    "Tasarım kaynağı",
    "Canlı sayfa için URL, sabit bir tasarım için ekran görüntüsü kullanın.",
  ],
  ["3", "Personalar", "Hedef kitlenizi temsil eden persona grubunu belirleyin."],
  ["4", "Analiz modülleri", "İhtiyacınız olan ek ölçümleri ve isterseniz AI raporunu seçin."],
  ["5", "Kontrol ve başlatma", "Toplam Chip bedelini kontrol edin, ardından testi başlatın."],
] as const;

const ANALYSIS_GUIDE = [
  {
    title: "Temel UX testi",
    useWhen: "Bir görevin ne kadar kolay tamamlandığını görmek istediğinizde",
    detail: "Görev tamamlama, sürtünme ve kullanıcı akışındaki temel sorunları özetler.",
  },
  {
    title: "Erişilebilirlik ön kontrolü",
    useWhen: "Canlı bir URL'nin erişilebilirlik risklerini taramak istediğinizde",
    detail: "DOM ve sayfa yapısını kontrol ettiği için yalnızca URL kaynağıyla çalışır.",
  },
  {
    title: "A/B tasarım karşılaştırması",
    useWhen: "Aynı ürünün mevcut ve değiştirilmiş tasarımını karşılaştırdığınızda",
    detail: "İki farklı şirketi değil, aynı deneyimin Tasarım A ve Tasarım B varyantlarını ölçer.",
  },
  {
    title: "AI raporu",
    useWhen: "Sonuçların persona ve senaryo bağlamında ayrıntılı yorumlanmasını istediğinizde",
    detail:
      "OpenAI ile çok aşamalı analiz üretir. Ücreti, testi başlatmadan önce özette gösterilir.",
  },
] as const;

const FAQ_ITEMS: FaqItem[] = [
  {
    category: "Test oluşturma",
    question: "URL ile ekran görüntüsü arasında nasıl seçim yapmalıyım?",
    answer:
      "Canlı sayfanın yapısı, bağlantıları ve erişilebilirliği incelenecekse URL kullanın. Yalnızca belirli bir ekranın görsel düzenini veya hazırladığınız bir varyantı test edecekseniz ekran görüntüsü kullanın. Ekran görüntüsünde DOM ve gerçek bağlantı verisi bulunmaz.",
  },
  {
    category: "Test oluşturma",
    question: "Yarım bıraktığım teste nasıl devam ederim?",
    answer:
      "Genel Bakış sayfasındaki “Yarım kalanları görüntüle” bağlantısından veya Raporlar sayfasındaki “Yarım kalan testler” sekmesinden taslağınızı açabilirsiniz. Sihirbaz, tamamladığınız son adımdan devam eder.",
  },
  {
    category: "Test oluşturma",
    question: "A/B testinde hangi iki tasarımı karşılaştırmalıyım?",
    answer:
      "Tasarım A mevcut ve kontrol sürümünüz, Tasarım B ise renk, yerleşim, metin veya CTA gibi kontrollü bir değişiklik yaptığınız yeni sürüm olmalıdır. İki farklı markanın sayfasını karşılaştırmak, A/B sonucunu anlamlandırmayı zorlaştırır.",
  },
  {
    category: "Analiz ve raporlar",
    question: "AI raporu ile Hızlı rapor özeti arasındaki fark nedir?",
    answer:
      "Hızlı rapor özeti yalnızca mevcut rapor metriklerini kısa ve okunabilir biçimde açıklar; yeni bir AI analizi üretmez ve ücretsizdir. AI raporu ise OpenAI kullanarak persona, senaryo, bulgu ve önerileri çok aşamalı biçimde analiz eder ve Chip ile ücretlendirilir.",
  },
  {
    category: "Analiz ve raporlar",
    question: "Isı haritasındaki renkler neyi gösterir?",
    answer:
      "Kırmızı alanlar göreve göre daha yüksek beklenen etkileşim veya dikkat yoğunluğunu, sarı orta yoğunluğu, yeşil ise daha düşük yoğunluğu gösterir. Bunlar sentetik tahminlerdir; gerçek kullanıcı tıklaması veya gerçek göz takibi kaydı değildir.",
  },
  {
    category: "Analiz ve raporlar",
    question: "Rapor neden hemen oluşmuyor?",
    answer:
      "Önce temel simülasyon tamamlanır, ardından seçilen analiz modülleri işlenir. AI raporu seçildiyse çok aşamalı işlem ayrıca yürütülür. Simülasyonlar sayfasından güncel durumu izleyebilirsiniz; başarısız görünen bir çalışmada hata mesajını kontrol edin.",
  },
  {
    category: "Chip ve ücretlendirme",
    question: "Ücretsiz kullanım hakkım bittikten sonra ne olur?",
    answer:
      "Ücretsiz hakkı olan modül ilk uygun kullanımda bu haktan düşer. Hak tüketildikten sonra güncel Chip bedeli Analiz Modülleri sayfasında ve test sihirbazındaki fiyat özetinde görünür. Test, son onayınız olmadan başlatılmaz.",
  },
  {
    category: "Chip ve ücretlendirme",
    question: "Tamamlanan bir testi neden silemiyorum?",
    answer:
      "Tamamlanan testlerde Chip harcaması ve buna bağlı rapor kaydı oluştuğu için sonuç bütünlüğü korunur. Yarım kalan taslaklar ve uygun durumdaki projeler silinebilir; tamamlanan kayıtlar rapor geçmişinde tutulur.",
  },
];

function normalizeQuery(value: string) {
  return value.trim().toLocaleLowerCase("tr-TR");
}

export default function Help() {
  const [query, setQuery] = useState("");
  const normalizedQuery = normalizeQuery(query);
  const filteredFaqs = useMemo(() => {
    if (!normalizedQuery) return FAQ_ITEMS;
    return FAQ_ITEMS.filter((item) =>
      normalizeQuery(`${item.category} ${item.question} ${item.answer}`).includes(normalizedQuery),
    );
  }, [normalizedQuery]);

  return (
    <section className="help-page" aria-labelledby="help-heading">
      <header className="help-hero">
        <div className="help-hero__icon" aria-hidden="true">
          <HelpCircleIcon />
        </div>
        <div>
          <p className="help-eyebrow">Yardım Merkezi</p>
          <h1 id="help-heading" className="page-heading">
            Nereden başlayacağınızı birlikte bulalım
          </h1>
          <p className="page-placeholder">
            Test oluşturmadan rapor yorumlamaya kadar Synthetix UX akışının kısa ve anlaşılır
            rehberi.
          </p>
        </div>
        <label className="help-search">
          <SearchIcon />
          <span className="sr-only">Sık sorulan sorularda ara</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="AI raporu, Chip, A/B veya yarım kalan test arayın"
          />
        </label>
      </header>

      <section className="help-section" aria-labelledby="help-quick-heading">
        <div className="help-section__heading">
          <div>
            <p className="help-eyebrow">Hızlı başlangıç</p>
            <h2 id="help-quick-heading">Yapmak istediğiniz işleme gidin</h2>
          </div>
        </div>
        <div className="help-quick-grid">
          {QUICK_LINKS.map((item) => {
            const Icon = item.icon;
            return (
              <Link key={item.to} to={item.to} className="help-quick-card">
                <span className="help-quick-card__icon">
                  <Icon />
                </span>
                <span className="help-quick-card__content">
                  <strong>{item.title}</strong>
                  <span>{item.description}</span>
                  <span className="help-quick-card__action">{item.action} →</span>
                </span>
              </Link>
            );
          })}
        </div>
      </section>

      <section className="help-section" aria-labelledby="help-workflow-heading">
        <div className="help-section__heading">
          <div>
            <p className="help-eyebrow">Test akışı</p>
            <h2 id="help-workflow-heading">Beş adımda yeni test</h2>
          </div>
          <Link to="/tests/new" className="btn-secondary help-section__link">
            Yeni test oluştur
          </Link>
        </div>
        <ol className="help-workflow">
          {WORKFLOW_STEPS.map(([number, title, description]) => (
            <li key={number}>
              <span className="help-workflow__number">{number}</span>
              <div>
                <strong>{title}</strong>
                <p>{description}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="help-section" aria-labelledby="help-analysis-heading">
        <div className="help-section__heading">
          <div>
            <p className="help-eyebrow">Analiz seçimi</p>
            <h2 id="help-analysis-heading">Hangi analizi ne zaman kullanmalısınız?</h2>
          </div>
          <Link to="/analiz-modulleri" className="btn-secondary help-section__link">
            Tüm modüller ve fiyatlar
          </Link>
        </div>
        <div className="help-analysis-grid">
          {ANALYSIS_GUIDE.map((item) => (
            <article key={item.title} className="help-analysis-card">
              <CheckCircleIcon />
              <div>
                <h3>{item.title}</h3>
                <p className="help-analysis-card__when">{item.useWhen}</p>
                <p>{item.detail}</p>
              </div>
            </article>
          ))}
        </div>
        <div className="help-info-note">
          <InfoIcon />
          <p>
            <strong>Hızlı rapor özeti</strong>, mevcut sonuçları sadeleştirir ve yeni AI analizi
            üretmez. Ayrıntılı yapay zekâ değerlendirmesi için test sırasında{" "}
            <strong>AI raporu</strong> modülünü seçin.
          </p>
        </div>
      </section>

      <section className="help-section" aria-labelledby="help-faq-heading">
        <div className="help-section__heading">
          <div>
            <p className="help-eyebrow">Sık sorulan sorular</p>
            <h2 id="help-faq-heading">Kısa yanıtlar</h2>
          </div>
          {normalizedQuery && (
            <span className="help-result-count" role="status">
              {filteredFaqs.length} sonuç
            </span>
          )}
        </div>
        <div className="help-faq-list">
          {filteredFaqs.map((item) => (
            <details key={item.question} className="help-faq-item">
              <summary>
                <span>
                  <small>{item.category}</small>
                  {item.question}
                </span>
                <span className="help-faq-item__plus" aria-hidden="true">
                  +
                </span>
              </summary>
              <p>{item.answer}</p>
            </details>
          ))}
          {filteredFaqs.length === 0 && (
            <div className="help-empty-search">
              <SearchIcon />
              <strong>Bu ifadeyle eşleşen bir yanıt bulunamadı.</strong>
              <p>Daha kısa bir kelime deneyin veya aşağıdaki sorun giderme bilgilerini kullanın.</p>
              <button type="button" className="btn-secondary" onClick={() => setQuery("")}>
                Aramayı temizle
              </button>
            </div>
          )}
        </div>
      </section>

      <section className="help-troubleshooting" aria-labelledby="help-troubleshooting-heading">
        <div>
          <p className="help-eyebrow">Sorun giderme</p>
          <h2 id="help-troubleshooting-heading">Bir şey beklediğiniz gibi çalışmıyorsa</h2>
          <p>
            Sorunu tekrar üretirken sayfanın adresini, proje ve test adını, yaklaşık saati ve
            görünen hata mesajını not edin. Bu bilgiler hangi aşamanın kontrol edilmesi gerektiğini
            hızla gösterir.
          </p>
        </div>
        <ul>
          <li>
            <CheckCircleIcon /> Simülasyonlar sayfasında çalışmanın durumunu kontrol edin.
          </li>
          <li>
            <CheckCircleIcon /> URL'nin herkese açık ve erişilebilir olduğundan emin olun.
          </li>
          <li>
            <CheckCircleIcon /> AI raporu için 3. adımda persona seçildiğini doğrulayın.
          </li>
          <li>
            <CheckCircleIcon /> Uzun süren işlemlerde sayfayı yenileyip güncel durumu tekrar açın.
          </li>
        </ul>
      </section>
    </section>
  );
}
