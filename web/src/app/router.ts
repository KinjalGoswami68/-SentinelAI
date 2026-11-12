import { useEffect, useState } from "react";

// Hash routes, so deep links work on GitHub Pages (no server-side rewrites):
//   #/                incidents     #/incidents/12   incident detail
//   #/health          bot health    #/setup          register + first answer
//   #/try             try without code (no key needed)
export type Route =
  | { name: "incidents" }
  | { name: "incident"; id: string }
  | { name: "health" }
  | { name: "setup" }
  | { name: "try" };

export function parse(hash: string): Route {
  const p = hash.replace(/^#/, "").replace(/\/+$/, "") || "/";
  const m = p.match(/^\/incidents\/(\d+)$/);
  if (m) return { name: "incident", id: m[1] };
  if (p === "/health") return { name: "health" };
  if (p === "/setup") return { name: "setup" };
  if (p === "/try") return { name: "try" };
  return { name: "incidents" };
}

export const href = (r: Route): string =>
  r.name === "incident" ? `#/incidents/${r.id}` : r.name === "incidents" ? "#/" : `#/${r.name}`;

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const on = () => { setRoute(parse(window.location.hash)); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
