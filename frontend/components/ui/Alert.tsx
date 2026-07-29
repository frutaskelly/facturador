import type { ReactNode } from "react";

type Tone = "info" | "success" | "warning" | "danger";

const TONES: Record<Tone, string> = {
  info: "border-blue-200 bg-blue-50 text-blue-800",
  success: "border-emerald-200 bg-emerald-50 text-emerald-800",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  danger: "border-red-200 bg-red-50 text-red-800",
};

export function Alert({
  tone = "info",
  title,
  children,
}: {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
}) {
  return (
    // role="alert" anuncia errores/avisos a lectores de pantalla; los tonos
    // informativos usan "status" (cortés, no interrumpe).
    <div
      role={tone === "danger" || tone === "warning" ? "alert" : "status"}
      className={`rounded-lg border px-3 py-2 text-sm ${TONES[tone]}`}
    >
      {title && <div className="font-medium">{title}</div>}
      {children && <div className={title ? "mt-0.5 opacity-90" : ""}>{children}</div>}
    </div>
  );
}
