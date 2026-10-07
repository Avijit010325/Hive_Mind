"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import AuthDialog from "@/components/AuthDialog";
import Landing from "@/components/Landing";
import { api, type User } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const [mode, setMode] = useState<"signup" | "login" | null>(null);

  // Signed-in visitors go straight to their crew.
  useEffect(() => {
    api<User>("GET", "/api/me").then(() => router.replace("/dashboard"), () => {});
  }, [router]);

  const openAuth = useCallback((next: "signup" | "login") => setMode(next), []);

  return (
    <>
      <Landing onAuth={openAuth} />
      <AuthDialog mode={mode} onMode={setMode} onDone={() => router.push("/dashboard")} />
    </>
  );
}
