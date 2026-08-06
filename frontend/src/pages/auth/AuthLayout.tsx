import type { ReactNode } from "react";
import { Link } from "react-router-dom";

interface AuthLayoutProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}

const BENEFITS = [
  "Dakikalar içinde senaryo bazlı tasarım geri bildirimi",
  "Esnek sentetik persona senaryolarıyla erken risk tespiti",
  "Ekiplerle paylaşılabilir, aksiyon alınabilir raporlar",
];

export default function AuthLayout({ title, subtitle, children, footer }: AuthLayoutProps) {
  return (
    <div className="auth-page">
      <aside className="auth-promo" aria-hidden="true">
        <Link to="/" className="auth-promo__brand">
          Synthetix UX
        </Link>

        <h2 className="auth-promo__headline">
          Tasarım risklerini gerçek kullanıcı araştırmasından önce sentetik senaryolarla keşfedin
        </h2>
        <p className="auth-promo__description">
          Synthetix UX, ürün kararlarınızı gerçek kullanıcılarla test etmeden önce yapay zekâ
          destekli sentetik personalarla sınar; tasarım risklerini erken ve düşük maliyetle
          yakalamanızı sağlar.
        </p>

        <svg
          className="auth-promo__illustration"
          viewBox="0 0 320 180"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          <line x1="60" y1="60" x2="160" y2="30" stroke="rgba(255,255,255,0.35)" strokeWidth="2" />
          <line x1="160" y1="30" x2="255" y2="70" stroke="rgba(255,255,255,0.35)" strokeWidth="2" />
          <line
            x1="160"
            y1="30"
            x2="120"
            y2="130"
            stroke="rgba(255,255,255,0.35)"
            strokeWidth="2"
          />
          <line
            x1="255"
            y1="70"
            x2="230"
            y2="150"
            stroke="rgba(255,255,255,0.35)"
            strokeWidth="2"
          />
          <line
            x1="120"
            y1="130"
            x2="230"
            y2="150"
            stroke="rgba(255,255,255,0.35)"
            strokeWidth="2"
          />
          <circle
            cx="60"
            cy="60"
            r="14"
            fill="rgba(255,255,255,0.18)"
            stroke="#fff"
            strokeWidth="2"
          />
          <circle
            cx="160"
            cy="30"
            r="18"
            fill="rgba(255,255,255,0.25)"
            stroke="#fff"
            strokeWidth="2"
          />
          <circle
            cx="255"
            cy="70"
            r="14"
            fill="rgba(255,255,255,0.18)"
            stroke="#fff"
            strokeWidth="2"
          />
          <circle
            cx="120"
            cy="130"
            r="16"
            fill="rgba(255,255,255,0.2)"
            stroke="#fff"
            strokeWidth="2"
          />
          <circle
            cx="230"
            cy="150"
            r="12"
            fill="rgba(255,255,255,0.18)"
            stroke="#fff"
            strokeWidth="2"
          />
        </svg>

        <ul className="auth-promo__benefits">
          {BENEFITS.map((benefit) => (
            <li key={benefit}>{benefit}</li>
          ))}
        </ul>

        <p className="auth-promo__disclaimer">
          Sonuçlar sentetik kullanıcı tahminleridir; gerçek kullanıcı araştırmasının yerini almaz.
        </p>
      </aside>

      <div className="auth-content">
        <div className="auth-card" role="main" aria-labelledby="auth-heading">
          <Link to="/" className="auth-card__brand">
            Synthetix UX
          </Link>
          <h1 id="auth-heading" className="auth-card__title">
            {title}
          </h1>
          {subtitle && <p className="auth-card__subtitle">{subtitle}</p>}
          {children}
          {footer && <div className="auth-card__footer">{footer}</div>}
        </div>
      </div>
    </div>
  );
}
