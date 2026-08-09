interface BrandLogoProps {
  className?: string;
}

export default function BrandLogo({ className = "" }: BrandLogoProps) {
  return (
    <span className={`brand-logo${className ? ` ${className}` : ""}`}>
      <span className="brand-logo__mark" aria-hidden="true">
        SX
      </span>
      <span className="brand-logo__text">Synthetix UX</span>
    </span>
  );
}
