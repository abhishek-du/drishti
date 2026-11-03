"use client";

import { useEffect, useState } from "react";

const THEME_KEY = "drishti.theme.v1";

export default function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    // plain string; an older chart build stored it JSON-quoted
    const saved = localStorage.getItem(THEME_KEY)?.replace(/"/g, "");
    const initial = saved === "light" ? "light" : "dark";
    setTheme(initial);
    document.documentElement.setAttribute("data-theme", initial);
  }, []);

  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(THEME_KEY, next);
      // Dispatch custom event so KChart or other components can sync if needed
      window.dispatchEvent(new CustomEvent("drishti:theme", { detail: next }));
    } catch {
      // storage blocked
    }
  };

  if (!mounted) {
    return <button className="theme-toggle" aria-label="Toggle theme" title="Toggle theme">🌙</button>;
  }

  return (
    <button
      className="theme-toggle"
      onClick={toggle}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      {theme === "dark" ? (
        <span className="icon">☀️</span>
      ) : (
        <span className="icon">🌙</span>
      )}
    </button>
  );
}
