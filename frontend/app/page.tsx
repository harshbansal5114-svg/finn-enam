"use client";

import Shell from "@/components/Shell";
import { AppProvider } from "@/lib/store";

export default function Page() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
