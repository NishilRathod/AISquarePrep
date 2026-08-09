import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { CitySuggestion } from "../api/types";
import { useAddCity, useCitySearch } from "../hooks/queries";
import { useDebounced } from "../hooks/useDebounced";
import { describeSuggestion, normalizeCity } from "../lib/format";

interface CitySearchBarProps {
  trackedCities: string[];
  onCityAdded: (city: string) => void;
}

export function CitySearchBar({ trackedCities, onCityAdded }: CitySearchBarProps) {
  const [query, setQuery] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  const containerRef = useRef<HTMLDivElement>(null);
  const listboxId = useId();

  const debouncedQuery = useDebounced(query, 250);
  const { data: suggestions = [], isFetching } = useCitySearch(debouncedQuery);
  const addCity = useAddCity();

  const trackedKeys = useMemo(
    () => new Set(trackedCities.map(normalizeCity)),
    [trackedCities],
  );

  const isTracked = (suggestion: CitySuggestion) => trackedKeys.has(normalizeCity(suggestion.name));

  // Reset the highlight whenever the result set changes underneath it.
  useEffect(() => setActiveIndex(0), [suggestions]);

  // Clicking anywhere else dismisses the list.
  useEffect(() => {
    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setIsOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  const select = (suggestion: CitySuggestion) => {
    if (isTracked(suggestion)) return; // Already tracked rows are inert.

    addCity.mutate(suggestion.name, {
      onSuccess: () => onCityAdded(suggestion.name),
    });
    setQuery("");
    setIsOpen(false);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      setIsOpen(false);
      return;
    }
    if (!isOpen || suggestions.length === 0) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % suggestions.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (index - 1 + suggestions.length) % suggestions.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const suggestion = suggestions[activeIndex];
      if (suggestion) select(suggestion);
    }
  };

  const trimmed = debouncedQuery.trim();
  const showList = isOpen && trimmed.length >= 2;
  const showEmpty = showList && !isFetching && suggestions.length === 0;

  return (
    <div ref={containerRef} className="relative">
      <label htmlFor={`${listboxId}-input`} className="sr-only">
        Search for a city to track
      </label>

      <input
        id={`${listboxId}-input`}
        type="text"
        role="combobox"
        autoComplete="off"
        aria-expanded={showList}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-activedescendant={
          showList && suggestions.length ? `${listboxId}-option-${activeIndex}` : undefined
        }
        placeholder="Search a city to track…"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setIsOpen(true);
        }}
        onFocus={() => setIsOpen(true)}
        onKeyDown={onKeyDown}
        className="w-full rounded-lg border px-4 py-2.5 text-sm outline-none transition-colors duration-150 placeholder:text-[var(--color-ink-faint)] focus:border-[var(--color-accent)]"
        style={{
          backgroundColor: "var(--color-panel)",
          borderColor: "var(--color-edge)",
          color: "var(--color-ink)",
        }}
      />

      {addCity.isError && (
        <p role="alert" className="mt-2 text-xs" style={{ color: "var(--color-danger)" }}>
          Couldn't add city: {addCity.error.message}
        </p>
      )}

      {showList && (
        <ul
          id={listboxId}
          role="listbox"
          aria-label="City suggestions"
          className="absolute z-20 mt-2 w-full overflow-hidden rounded-lg border shadow-2xl"
          style={{ backgroundColor: "var(--color-panel)", borderColor: "var(--color-edge-strong)" }}
        >
          {suggestions.map((suggestion, index) => {
            const tracked = isTracked(suggestion);
            const active = index === activeIndex;

            return (
              <li
                key={`${suggestion.name}-${suggestion.state}-${suggestion.country}`}
                id={`${listboxId}-option-${index}`}
                role="option"
                aria-selected={active}
                aria-disabled={tracked}
                title={tracked ? "Already tracked" : `Track ${suggestion.name}`}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => select(suggestion)}
                className="flex items-center justify-between gap-3 border-b px-4 py-2.5 text-sm last:border-b-0 transition-colors duration-100"
                style={{
                  borderColor: "var(--color-edge)",
                  // Tracked rows read as green and refuse the click; only
                  // selectable rows get the accent highlight.
                  backgroundColor: tracked
                    ? "rgb(56 211 159 / 0.14)"
                    : active
                      ? "var(--color-panel-raised)"
                      : "transparent",
                  color: tracked ? "var(--color-live)" : "var(--color-ink)",
                  cursor: tracked ? "not-allowed" : "pointer",
                }}
              >
                <span className="truncate">
                  {describeSuggestion(suggestion.name, suggestion.state, suggestion.country)}
                </span>
                {tracked && (
                  <span className="shrink-0 text-[10px] font-semibold tracking-wider uppercase">
                    Tracked
                  </span>
                )}
              </li>
            );
          })}

          {showEmpty && (
            <li className="px-4 py-3 text-sm" style={{ color: "var(--color-ink-faint)" }}>
              No cities match “{trimmed}”.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
