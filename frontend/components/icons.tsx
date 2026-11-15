type P = { className?: string };
const base = { width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

export const PulseIcon = ({ className }: P) => (
  <svg {...base} className={className} aria-hidden>
    <path d="M3 12h4l2.5-7 5 14 2.5-7H21" />
  </svg>
);
export const ResearchIcon = ({ className }: P) => (
  <svg {...base} className={className} aria-hidden>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-4.2-4.2M8.5 11h5M11 8.5v5" />
  </svg>
);
export const SystemIcon = ({ className }: P) => (
  <svg {...base} className={className} aria-hidden>
    <path d="M4 7h10M18 7h2M4 17h2M10 17h10" />
    <circle cx="16" cy="7" r="2" />
    <circle cx="8" cy="17" r="2" />
  </svg>
);
export const ExternalIcon = ({ className }: P) => (
  <svg {...base} width={11} height={11} strokeWidth={2.2} className={className} aria-hidden>
    <path d="M14 4h6v6M20 4 10 14M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
  </svg>
);
export const SearchIcon = ({ className }: P) => (
  <svg {...base} width={16} height={16} className={className} aria-hidden>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-4.2-4.2" />
  </svg>
);
export const WarnIcon = ({ className }: P) => (
  <svg {...base} width={13} height={13} strokeWidth={2.2} className={className} aria-hidden>
    <path d="M12 3 2.5 20h19L12 3Z" />
    <path d="M12 10v4.5M12 17.5v.01" />
  </svg>
);
