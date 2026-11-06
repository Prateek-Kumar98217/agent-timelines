import type { Metadata } from "next";
import { Overpass, Overpass_Mono } from "next/font/google";
import type { ReactNode } from "react";
import "./globals.css";

const sans = Overpass({ subsets: ["latin"], variable: "--font-sans" });
const mono = Overpass_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "agent-timelines",
  description: "Run an agent task, change any step, and compare what happens.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
