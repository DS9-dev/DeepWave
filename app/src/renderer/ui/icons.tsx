// Набор линейных иконок (SVG, 24×24, цвет — currentColor).
type P = { size?: number; className?: string };
const S = ({ size = 20, className, children, fill }: P & { children: React.ReactNode; fill?: boolean }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" className={className} aria-hidden="true"
    fill={fill ? "currentColor" : "none"} stroke={fill ? "none" : "currentColor"} strokeWidth={1.8}
    strokeLinecap="round" strokeLinejoin="round">{children}</svg>
);

export const IPlay = (p: P) => <S {...p} fill><path d="M8 5.2v13.6a1 1 0 0 0 1.5.86l11-6.8a1 1 0 0 0 0-1.72l-11-6.8A1 1 0 0 0 8 5.2Z" /></S>;
export const IPause = (p: P) => <S {...p} fill><rect x="6" y="4.5" width="4.2" height="15" rx="1.3" /><rect x="13.8" y="4.5" width="4.2" height="15" rx="1.3" /></S>;
export const INext = (p: P) => <S {...p} fill><path d="M4 6.2v11.6a.9.9 0 0 0 1.4.75L13 13.4v4.4a.9.9 0 0 0 1.4.75l8.4-5.8a.9.9 0 0 0 0-1.5l-8.4-5.8A.9.9 0 0 0 13 6.2v4.4L5.4 5.45A.9.9 0 0 0 4 6.2Z" /></S>;
export const IPrev = (p: P) => <S {...p} fill><path d="M20 6.2v11.6a.9.9 0 0 1-1.4.75L11 13.4v4.4a.9.9 0 0 1-1.4.75L1.2 12.75a.9.9 0 0 1 0-1.5L9.6 5.45A.9.9 0 0 1 11 6.2v4.4l7.6-5.15A.9.9 0 0 1 20 6.2Z" /></S>;
export const IShuffle = (p: P) => <S {...p}><path d="M3 7h3.5c2.5 0 3.7 1.4 5 3.5l1 1.5c1.3 2.1 2.5 3.5 5 3.5H21" /><path d="M3 17h3.5c1.4 0 2.4-.45 3.2-1.2M14.3 8.2C15.1 7.45 16.1 7 17.5 7H21" /><path d="m18 4 3 3-3 3M18 14l3 3-3 3" /></S>;
export const IListen = (p: P) => <S {...p}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="2.6" /><path d="M12 3a9 9 0 0 1 9 9" opacity=".5" /></S>;
export const ILibrary = (p: P) => <S {...p}><path d="M5 4v16M9.5 4v16" /><path d="m14 4.6 4.3-1.1 3.2 16-4.3 1Z" /></S>;
export const IHistory = (p: P) => <S {...p}><path d="M3.5 12a8.5 8.5 0 1 0 2.5-6" /><path d="M3 4v4.5h4.5" /><path d="M12 7.5V12l3 2" /></S>;
export const ICatalog = (p: P) => <S {...p}><ellipse cx="12" cy="5.5" rx="7.5" ry="2.8" /><path d="M4.5 5.5v6.5c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8V5.5" /><path d="M4.5 12v6.5c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8V12" /></S>;
export const ISettings = (p: P) => <S {...p}><path d="M4 7h10M18 7h2M4 17h4M12 17h8" /><circle cx="16" cy="7" r="2.2" /><circle cx="10" cy="17" r="2.2" /></S>;
export const ISearch = (p: P) => <S {...p}><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.4-4.4" /></S>;
export const IVolume = (p: P) => <S {...p}><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5Z" fill="currentColor" /><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" /></S>;
export const IMute = (p: P) => <S {...p}><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5Z" fill="currentColor" /><path d="m16 9.5 5 5M21 9.5l-5 5" /></S>;
export const IBack = (p: P) => <S {...p}><path d="M15 5l-7 7 7 7" /></S>;
export const IStop = (p: P) => <S {...p} fill><rect x="6" y="6" width="12" height="12" rx="2.5" /></S>;
export const ICheck = (p: P) => <S {...p}><path d="m5 12.5 4.2 4.2L19 7" /></S>;
export const IDisc = (p: P) => <S {...p}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="3" /></S>;
export const IUser = (p: P) => <S {...p}><circle cx="12" cy="8.5" r="3.8" /><path d="M4.5 20c.9-3.6 3.9-5.6 7.5-5.6s6.6 2 7.5 5.6" /></S>;
export const IExternal = (p: P) => <S {...p}><path d="M14 4h6v6M20 4l-9 9" /><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10" /></S>;
export const ICompact = (p: P) => <S {...p}><rect x="3.5" y="13" width="10" height="7" rx="1.8" /><path d="M20.5 9.5V5.3c0-1-.8-1.8-1.8-1.8H6.3c-1 0-1.8.8-1.8 1.8V9" /><path d="m17 8.5-3.5 3.5M13.5 8.8V12h3.2" /></S>;
export const IExpand = (p: P) => <S {...p}><path d="M14 4h6v6M10 20H4v-6M20 4l-6.5 6.5M4 20l6.5-6.5" /></S>;
export const IPin = (p: P) => <S {...p}><path d="M9 4h6l-1 5 3.5 3.5v1.5h-11v-1.5L10 9Z" /><path d="M12 14v6" /></S>;
export const IClose = (p: P) => <S {...p}><path d="M6 6l12 12M18 6 6 18" /></S>;
export const IWave = (p: P) => <S {...p}><path d="M3 12h2M7 8v8M11 5v14M15 9v6M19 7v10M21 12h0" /></S>;
