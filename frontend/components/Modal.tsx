"use client";

import { useEffect, useRef } from "react";

type Props = {
  open: boolean;
  onClose: () => void;
  className?: string;
  children: React.ReactNode;
  dialogRef?: React.RefObject<HTMLDialogElement | null>;
};

/** A native <dialog> driven by an `open` prop (focus trap, Esc and backdrop come for free). */
export default function Modal({ open, onClose, className = "", children, dialogRef }: Props) {
  const own = useRef<HTMLDialogElement>(null);
  const ref = dialogRef ?? own;

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open, ref]);

  return (
    <dialog ref={ref} className={`sheet ${className}`} onClose={onClose}>
      {open ? children : null}
    </dialog>
  );
}
