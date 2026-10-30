export default function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className="drishti-logo"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="drishtiGlow" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#3b82f6" />
          <stop offset="50%" stopColor="#6366f1" />
          <stop offset="100%" stopColor="#10b981" />
        </linearGradient>
        <linearGradient id="irisGrad" x1="10" y1="10" x2="22" y2="22" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#60a5fa" />
          <stop offset="100%" stopColor="#34d399" />
        </linearGradient>
        <filter id="logoGlow" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="1" stdDeviation="2" floodColor="#3b82f6" floodOpacity="0.35" />
        </filter>
      </defs>
      
      {/* Outer Eye / Aperture Lens with subtle glow */}
      <path
        d="M2.5 16C6 8.5 11 4.5 16 4.5C21 4.5 26 8.5 29.5 16C26 23.5 21 27.5 16 27.5C11 27.5 6 23.5 2.5 16Z"
        stroke="url(#drishtiGlow)"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="rgba(59, 130, 246, 0.08)"
        filter="url(#logoGlow)"
      />
      
      {/* Precision Focus Ring */}
      <circle
        cx="16"
        cy="16"
        r="6.5"
        stroke="url(#drishtiGlow)"
        strokeWidth="1.8"
        strokeDasharray="28 4"
      />
      
      {/* Core Pupil / Market Focus Beacon */}
      <circle cx="16" cy="16" r="3.2" fill="url(#irisGrad)" />
      
      {/* Precision Reticle Marks */}
      <line x1="16" y1="2" x2="16" y2="5" stroke="#60a5fa" strokeWidth="2" strokeLinecap="round" />
      <line x1="16" y1="27" x2="16" y2="30" stroke="#34d399" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
