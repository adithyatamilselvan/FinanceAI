import type { SVGProps } from "react";

// A tiny inline icon set (24x24 stroke icons, Lucide-style geometry). No icon library dependency.
type P = SVGProps<SVGSVGElement>;
const base = (p: P) => ({ width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true, focusable: false, ...p });

export const IconDashboard = (p: P) => <svg {...base(p)}><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></svg>;
export const IconList = (p: P) => <svg {...base(p)}><path d="M8 6h13M8 12h13M8 18h13" /><circle cx="3.5" cy="6" r="1" /><circle cx="3.5" cy="12" r="1" /><circle cx="3.5" cy="18" r="1" /></svg>;
export const IconUpload = (p: P) => <svg {...base(p)}><path d="M12 15V4M7.5 8.5 12 4l4.5 4.5" /><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" /></svg>;
export const IconScan = (p: P) => <svg {...base(p)}><path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2" /><path d="M7 12h10" /></svg>;
export const IconWallet = (p: P) => <svg {...base(p)}><path d="M3 7a2 2 0 0 1 2-2h13v4" /><path d="M3 7v11a2 2 0 0 0 2 2h14a1 1 0 0 0 1-1v-9a1 1 0 0 0-1-1H5a2 2 0 0 1-2-2Z" /><circle cx="16.5" cy="14.5" r="1.2" /></svg>;
export const IconTarget = (p: P) => <svg {...base(p)}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /></svg>;
export const IconChart = (p: P) => <svg {...base(p)}><path d="M4 20V10M10 20V4M16 20v-8M22 20H2" /></svg>;
export const IconSliders = (p: P) => <svg {...base(p)}><path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h12M20 18h0" /><circle cx="16" cy="6" r="2" /><circle cx="8" cy="12" r="2" /><circle cx="18" cy="18" r="2" /></svg>;
export const IconFile = (p: P) => <svg {...base(p)}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></svg>;
export const IconUser = (p: P) => <svg {...base(p)}><circle cx="12" cy="8" r="4" /><path d="M4 20a8 8 0 0 1 16 0" /></svg>;
export const IconLogout = (p: P) => <svg {...base(p)}><path d="M9 21H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3M16 17l5-5-5-5M21 12H9" /></svg>;
export const IconMenu = (p: P) => <svg {...base(p)}><path d="M4 6h16M4 12h16M4 18h16" /></svg>;
export const IconClose = (p: P) => <svg {...base(p)}><path d="M6 6l12 12M18 6 6 18" /></svg>;
export const IconChevronLeft = (p: P) => <svg {...base(p)}><path d="m15 6-6 6 6 6" /></svg>;
export const IconChevronRight = (p: P) => <svg {...base(p)}><path d="m9 6 6 6-6 6" /></svg>;
export const IconSun = (p: P) => <svg {...base(p)}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>;
export const IconMoon = (p: P) => <svg {...base(p)}><path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z" /></svg>;
export const IconPlus = (p: P) => <svg {...base(p)}><path d="M12 5v14M5 12h14" /></svg>;
export const IconArrowUp = (p: P) => <svg {...base(p)}><path d="M12 19V5M6 11l6-6 6 6" /></svg>;
export const IconArrowDown = (p: P) => <svg {...base(p)}><path d="M12 5v14M6 13l6 6 6-6" /></svg>;
export const IconSwap = (p: P) => <svg {...base(p)}><path d="M7 4 3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7" /></svg>;
export const IconAlert = (p: P) => <svg {...base(p)}><path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /><path d="M12 9v4M12 17h0" /></svg>;
export const IconCheck = (p: P) => <svg {...base(p)}><path d="m5 12 5 5L20 7" /></svg>;
export const IconInfo = (p: P) => <svg {...base(p)}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h0" /></svg>;
export const IconRepeat = (p: P) => <svg {...base(p)}><path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 0 1-3 3H3" /></svg>;
export const IconShield = (p: P) => <svg {...base(p)}><path d="M12 3 4 6v6c0 4.5 3.2 8 8 9 4.8-1 8-4.5 8-9V6Z" /><path d="m9 12 2 2 4-4" /></svg>;
export const IconDownload = (p: P) => <svg {...base(p)}><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5" /><path d="M4 18v1a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-1" /></svg>;
export const IconSearch = (p: P) => <svg {...base(p)}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>;
export const IconEdit = (p: P) => <svg {...base(p)}><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16Z" /></svg>;
export const IconTrash = (p: P) => <svg {...base(p)}><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" /></svg>;
export const IconLogo = (p: P) => (
  <svg width={28} height={28} viewBox="0 0 32 32" aria-hidden focusable={false} {...p}>
    <rect width="32" height="32" rx="8" fill="var(--color-blue-600)" />
    <path d="M9 22V10h10M9 16h7" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" fill="none" />
    <path d="M17 22l3-4 2 2 3-5" stroke="#9ff0c4" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
  </svg>
);
