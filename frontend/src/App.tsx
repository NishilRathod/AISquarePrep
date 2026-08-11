import { Route, Routes } from "react-router-dom";
import { API_BASE_URL } from "./api/client";
import AnomaliesPage from "./pages/AnomaliesPage";
import HomePage from "./pages/HomePage";

/**
 * Router shell: the chrome every page shares.
 *
 * The two pages are deliberately independent. Home reads the tracked cities from
 * OpenWeather via Redis; Anomalies reads a global board computed on a server-side
 * timer. Neither one's failure should be able to blank the other, so they own
 * their own loading and error states rather than sharing one.
 */
export default function App() {
  return (
    <div className="relative min-h-dvh">
      <div className="relative mx-auto max-w-6xl px-5 py-6 sm:px-8">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/anomalies" element={<AnomaliesPage />} />
          {/* Anything else is Home rather than a dead end. */}
          <Route path="*" element={<HomePage />} />
        </Routes>

        <footer
          className="mt-6 border-t pt-3 text-[11px] leading-relaxed"
          style={{ borderColor: "var(--color-edge)", color: "var(--color-ink-faint)" }}
        >
          <p>
            Cache-aside: <span style={{ color: "var(--color-cached)" }}>CACHED</span> came from
            Redis, <span style={{ color: "var(--color-live)" }}>LIVE</span> was just fetched from
            OpenWeather and stored — so loading this page can populate the cache rather than only
            observe it. Entries expire after 10 minutes. Pins are per-browser; tracked cities live
            on the server. <code>{API_BASE_URL}</code> · City data ©{" "}
            <a
              href="https://www.geonames.org/"
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2"
            >
              GeoNames
            </a>{" "}
            (CC BY 4.0) · Anomaly baselines from{" "}
            <a
              href="https://open-meteo.com/"
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2"
            >
              Open-Meteo
            </a>{" "}
            (ERA5, CC BY 4.0).
          </p>
        </footer>
      </div>
    </div>
  );
}
