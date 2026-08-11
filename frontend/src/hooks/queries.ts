import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addCity,
  ApiError,
  fetchAnomalies,
  fetchHealth,
  fetchTrackedCities,
  fetchWeather,
  searchCities,
} from "../api/client";
import type { TrackedCities } from "../api/types";
import { writeTrackedMirror } from "../lib/storage";

export const queryKeys = {
  tracked: ["cities"] as const,
  weather: (cities: string[]) => ["weather", cities] as const,
  search: (query: string) => ["cities", "search", query] as const,
  health: ["health"] as const,
  anomalies: (limit: number) => ["anomalies", limit] as const,
};

export function useTrackedCities() {
  return useQuery({
    queryKey: queryKeys.tracked,
    queryFn: fetchTrackedCities,
    staleTime: 30_000,
  });
}

/** Weather for exactly the cities on the current page. */
export function useWeather(cities: string[]) {
  return useQuery({
    queryKey: queryKeys.weather(cities),
    queryFn: () => fetchWeather(cities),
    enabled: cities.length > 0,
    staleTime: 30_000,
  });
}

export function useCitySearch(query: string) {
  return useQuery({
    queryKey: queryKeys.search(query),
    queryFn: ({ signal }) => searchCities(query, signal),
    enabled: query.trim().length >= 2,
    staleTime: 5 * 60_000,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: fetchHealth,
    // A 503 here is the answer, not a fluke worth retrying.
    retry: false,
    refetchInterval: 60_000,
  });
}

/**
 * The global anomaly board.
 *
 * Deliberately isolated from the dashboard's error handling: this query's
 * failure must never reach the `error` branch in App, which replaces the whole
 * grid with an ErrorState. A board that cannot load is a missing section, not a
 * broken dashboard. The server sweeps on a multi-hour timer, so polling harder
 * than that would only re-fetch an identical board.
 */
export function useAnomalies(limit = 10) {
  return useQuery({
    queryKey: queryKeys.anomalies(limit),
    queryFn: () => fetchAnomalies(limit),
    staleTime: 15 * 60_000,
    refetchInterval: 15 * 60_000,
    retry: false,
  });
}

export function useAddCity() {
  const queryClient = useQueryClient();

  return useMutation<Awaited<ReturnType<typeof addCity>>, ApiError, string>({
    mutationFn: addCity,
    // Show the city immediately; the server list is authoritative once it answers.
    onMutate: async (city) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.tracked });
      const previous = queryClient.getQueryData<TrackedCities>(queryKeys.tracked);

      if (previous) {
        queryClient.setQueryData<TrackedCities>(queryKeys.tracked, {
          ...previous,
          cities: [...previous.cities, city],
        });
      }
      return { previous };
    },
    onError: (_error, _city, context) => {
      const previous = (context as { previous?: TrackedCities } | undefined)?.previous;
      if (previous) {
        queryClient.setQueryData(queryKeys.tracked, previous);
      }
    },
    onSuccess: (result) => {
      queryClient.setQueryData<TrackedCities>(queryKeys.tracked, (current) =>
        current ? { ...current, cities: result.cities } : current,
      );
      writeTrackedMirror(result.cities);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.tracked });
      void queryClient.invalidateQueries({ queryKey: ["weather"] });
    },
  });
}
