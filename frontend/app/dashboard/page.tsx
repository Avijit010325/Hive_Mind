"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Dashboard from "@/components/Dashboard";
import { api, type User } from "@/lib/api";

export default function DashboardPage() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    api<User>("GET", "/api/me").then(setUser, async () => {
      // Not signed in, or the session expired: clear any stale cookie and go home.
      await api("POST", "/api/auth/logout").catch(() => {});
      router.replace("/");
    });
  }, [router]);

  async function logout() {
    await api("POST", "/api/auth/logout").catch(() => {});
    router.replace("/");
  }

  if (!user) return <div className="center"><span className="spinner" /></div>;
  return <Dashboard user={user} onLogout={logout} />;
}
