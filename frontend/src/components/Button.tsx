import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

export function Button({ variant = "ghost", className = "", type, ...props }: ButtonProps) {
  const variantClass = variant === "primary" ? "btn-primary" : "btn-ghost";
  return (
    <button
      type={type ?? "button"}
      className={`btn ${variantClass} ${className}`.trim()}
      {...props}
    />
  );
}
