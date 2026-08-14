/**
 * Marca de Facturador Inteligente.
 *
 * `LogoMark` = el símbolo (F azul con palomita verde = validado/timbrado).
 * `Logo` = símbolo + wordmark "Facturador Inteligente".
 *
 * Colores fijos de marca (azul #2C3E50 + verde #82CF5D), no dependen del tema
 * porque siempre se pintan sobre superficies claras.
 */

const NAVY = "#2C3E50";
const GREEN = "#82CF5D";
const GREEN_DARK = "#6bbd45";

export function LogoMark({ size = 30, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className={className}
    >
      <path d="M10 6 H37 V14.5 H18.5 V21.5 H33 V30 H18.5 V42 H10 Z" fill={NAVY} />
      <path
        d="M12.5 27 L21 35.5 L42 8.5"
        stroke={GREEN}
        strokeWidth="6.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <span className={`font-semibold tracking-tight ${className}`} style={{ color: NAVY }}>
      Facturador <span style={{ color: GREEN_DARK }}>Inteligente</span>
    </span>
  );
}

export function Logo({
  size = 30,
  wordmarkClassName = "",
  className = "",
}: {
  size?: number;
  wordmarkClassName?: string;
  className?: string;
}) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <LogoMark size={size} />
      <Wordmark className={wordmarkClassName} />
    </span>
  );
}
