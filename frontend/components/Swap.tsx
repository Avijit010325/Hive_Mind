"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { EASE, prefersStill } from "@/lib/motion";

type Props = { text: string; as?: React.ElementType; id?: string; className?: string };

/** Text that cross-fades when it changes instead of swapping instantly. */
export default function Swap({ text, as: Tag = "span", ...rest }: Props) {
  const ref = useRef<HTMLElement>(null);
  const [shown, setShown] = useState(text);
  const first = useRef(true);

  useEffect(() => {
    if (text === shown) return;
    const node = ref.current;
    if (!node || !shown || prefersStill()) {
      setShown(text);
      return;
    }
    let cancelled = false;
    const out = node.animate(
      { opacity: [1, 0], transform: ["none", "translateY(-5px)"] },
      { duration: 170, easing: "ease-in", fill: "forwards" },
    );
    out.finished.then(() => { if (!cancelled) { out.cancel(); setShown(text); } }, () => {});
    return () => { cancelled = true; out.cancel(); };
  }, [text, shown]);

  useLayoutEffect(() => {
    if (first.current) { first.current = false; return; }
    if (prefersStill()) return;
    ref.current?.animate({ opacity: [0, 1], transform: ["translateY(7px)", "none"] }, { duration: 380, easing: EASE });
  }, [shown]);

  return <Tag ref={ref} {...rest}>{shown}</Tag>;
}
