export type WeatherSource = "cache" | "upstream";

export interface Weather {
  city: string;
  country: string | null;
  temperature_c: number;
  feels_like_c: number;
  humidity_pct: number;
  condition: string;
  wind_speed_mps: number;
  observed_at: string;
  source: WeatherSource;
}

export interface PaginatedWeather {
  items: Weather[];
  page: number;
  page_size: number;
  total: number;
}

export interface CitySuggestion {
  name: string;
  state: string;
  country: string;
  population: number;
}

export interface TrackedCities {
  cities: string[];
  defaults: string[];
}

export interface AddCityResult {
  city: string;
  added: boolean;
  cities: string[];
}

export interface Health {
  status: "ok" | "error";
  redis: "connected" | "unreachable";
}

export type AnomalyDriver = "temperature" | "humidity";
export type AnomalyDirection = "above" | "below";

/**
 * One city on the global anomaly board. Carries the normal and the standard
 * deviation alongside the observation so a reader can recompute `z_score`
 * from the row itself rather than taking the ranking on trust.
 */
export interface AnomalyRow {
  rank: number;
  city: string;
  state: string;
  country: string;
  latitude: number;
  longitude: number;
  temperature_c: number;
  humidity_pct: number;
  normal_temperature_c: number;
  normal_humidity_pct: number;
  sd_temperature_c: number;
  sd_humidity_pct: number;
  z_temperature: number;
  z_humidity: number;
  z_score: number;
  driver: AnomalyDriver;
  direction: AnomalyDirection;
}

export interface SynopticEvent {
  name: string;
  cities: string[];
  explanation: string;
}

export interface CityNote {
  city: string;
  significance: "notable" | "routine" | "health_risk";
  note: string;
}

export interface AnomalyBriefing {
  headline: string;
  events: SynopticEvent[];
  notes: CityNote[];
  suspect_readings: string[];
}

export interface AnomalyBoard {
  rows: AnomalyRow[];
  /** Null whenever the interpretation layer is unavailable. The rows stand alone. */
  briefing: AnomalyBriefing | null;
  swept_at: string | null;
  cities_scored: number;
  source: "fresh" | "stale" | "unavailable";
}
