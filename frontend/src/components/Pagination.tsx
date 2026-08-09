import { Button } from "./Button";

interface PaginationProps {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
}

export function Pagination({ page, pageCount, onChange }: PaginationProps) {
  if (pageCount <= 1) return null;

  const pages = Array.from({ length: pageCount }, (_, index) => index + 1);

  return (
    <nav className="flex flex-wrap items-center justify-center gap-2" aria-label="Pagination">
      <Button onClick={() => onChange(page - 1)} disabled={page === 1}>
        ← Prev
      </Button>

      {pages.map((value) => (
        <Button
          key={value}
          onClick={() => onChange(value)}
          aria-current={value === page ? "page" : undefined}
          className="!px-3 tabular-nums"
          style={
            value === page
              ? {
                  borderColor: "var(--color-accent)",
                  color: "var(--color-accent-strong)",
                  backgroundColor: "rgb(91 140 255 / 0.12)",
                }
              : undefined
          }
        >
          {value}
        </Button>
      ))}

      <Button onClick={() => onChange(page + 1)} disabled={page === pageCount}>
        Next →
      </Button>
    </nav>
  );
}
