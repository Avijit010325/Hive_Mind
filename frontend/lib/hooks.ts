"use client";

import { useEffect, useRef } from "react";

/**
 * Tells which ids are being drawn for the first time, so only genuinely new
 * items play an entrance animation (refreshing a list must not replay them).
 */
export function useFresh(ids: number[]) {
  const seen = useRef(new Set<number>());
  useEffect(() => {
    ids.forEach((id) => seen.current.add(id));
  });
  return (id: number) => !seen.current.has(id);
}
