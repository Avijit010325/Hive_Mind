export const EASE = "cubic-bezier(.2, .7, .2, 1)";

export const prefersStill = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
