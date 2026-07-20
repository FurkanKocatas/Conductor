import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Board from "./routes/Board";

/** Placeholder surfaces — built out in the next slice. */
function Soon({ name }: { name: string }) {
  return (
    <div style={{ display: "grid", placeItems: "center", height: "100%", color: "var(--text-faint)" }}>
      {name} — coming next
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/board" replace />} />
        <Route path="/board" element={<Board />} />
        <Route path="/demo" element={<Board demo />} />
        <Route path="/memory" element={<Soon name="Memory" />} />
        <Route path="/analytics" element={<Soon name="Analytics" />} />
        <Route path="*" element={<Navigate to="/board" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
