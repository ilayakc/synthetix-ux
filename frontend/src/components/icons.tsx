import type { SVGProps } from "react";

/**
 * Uygulama genelinde kullanilan kucuk, tutarli cizgi ikon seti (Feather
 * tarzi: 20x20, stroke=currentColor, dolgu yok). Harici bir ikon
 * kutuphanesi eklemek yerine (bkz. package.json - hicbir ikon paketi
 * yuklu degil) elle yazilmis SVG'ler kullanilir; boylece tema rengine
 * (currentColor) otomatik uyum saglar ve bundle boyutu artmaz.
 */
type IconProps = SVGProps<SVGSVGElement>;

const base = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  focusable: "false" as const,
  "aria-hidden": true,
};

export function HomeIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M3 11.5 12 4l9 7.5" />
      <path d="M5.5 10v9a1 1 0 0 0 1 1H10v-6h4v6h3.5a1 1 0 0 0 1-1v-9" />
    </svg>
  );
}

export function FolderIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M3.5 6.5A1.5 1.5 0 0 1 5 5h4l2 2.5h8A1.5 1.5 0 0 1 20.5 9v8.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5Z" />
    </svg>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function UsersIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="9" cy="8" r="3.25" />
      <path d="M3.5 19c.6-3 2.7-4.75 5.5-4.75s4.9 1.75 5.5 4.75" />
      <path d="M15.5 5.75a3.25 3.25 0 0 1 0 6.35" />
      <path d="M17 14.4c2.35.4 3.9 2 4.4 4.6" />
    </svg>
  );
}

export function LayersIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="m12 3.5 8 4.25L12 12l-8-4.25Z" />
      <path d="m4 12 8 4.25L20 12" />
      <path d="m4 15.75 8 4.25 8-4.25" />
    </svg>
  );
}

export function ActivityIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M3 12h4l2.5 7 4-14 2.5 7H21" />
    </svg>
  );
}

export function FileTextIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M7 3.5h7l4 4V19a1.25 1.25 0 0 1-1.25 1.25H7A1.25 1.25 0 0 1 5.75 19V4.75A1.25 1.25 0 0 1 7 3.5Z" />
      <path d="M14 3.5V8h4.25" />
      <path d="M8.5 12.5h7M8.5 16h7" />
    </svg>
  );
}

export function ChipCoinIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.25" />
      <path d="M12 8.25v7.5M9 10.25h3.5a1.75 1.75 0 1 1 0 3.5H9M14.5 13.75H10" />
    </svg>
  );
}

export function GearIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="2.75" />
      <path d="M12 3.75v2.1M12 18.15v2.1M20.25 12h-2.1M5.85 12h-2.1M17.7 6.3l-1.5 1.5M7.8 16.2l-1.5 1.5M17.7 17.7l-1.5-1.5M7.8 7.8l-1.5-1.5" />
    </svg>
  );
}

export function HelpCircleIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.25" />
      <path d="M9.5 9.25a2.5 2.5 0 1 1 3.5 2.29c-.75.35-1 .82-1 1.46v.4" />
      <path d="M12 16.75h.01" />
    </svg>
  );
}

export function SearchIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="10.75" cy="10.75" r="6.25" />
      <path d="m19.5 19.5-4-4" />
    </svg>
  );
}

export function BellIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M6 10.5a6 6 0 0 1 12 0c0 4 1.25 5.25 1.25 5.25H4.75S6 14.5 6 10.5Z" />
      <path d="M10 18.5a2 2 0 0 0 4 0" />
    </svg>
  );
}

export function MenuIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

export function ShieldCheckIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3.75 19 6.5v5.25c0 4.5-3 7.75-7 9-4-1.25-7-4.5-7-9V6.5Z" />
      <path d="m9 12 2 2 4-4.25" />
    </svg>
  );
}

export function TicketIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M4 9.5a1.75 1.75 0 0 0 0-3.4V5a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v1.1a1.75 1.75 0 0 0 0 3.4V9.9a1.75 1.75 0 0 0 0 3.4v1.1a1.75 1.75 0 0 0 0 3.4V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-1.1a1.75 1.75 0 0 0 0-3.4v-1.1a1.75 1.75 0 0 0 0-3.4Z" />
      <path d="M12 4v16" strokeDasharray="2.5 2.5" />
    </svg>
  );
}

export function CheckCircleIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.25" />
      <path d="m8.25 12.25 2.5 2.5 5-5.5" />
    </svg>
  );
}

export function InfoIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.25" />
      <path d="M12 11v5.25M12 8.25h.01" />
    </svg>
  );
}
