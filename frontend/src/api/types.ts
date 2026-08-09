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
