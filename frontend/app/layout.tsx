import type { Metadata, Viewport } from "next";
import "./globals.css";
import { PwaRegister } from "../components/PwaRegister";
export const metadata: Metadata = {
  title: "Проход · контроль объекта",
  description: "Смены, проходная и присутствие на строительной площадке",
  applicationName: "Проход",
  manifest: "/manifest.json",
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
    ],
    apple: "/icons/apple-touch-icon.png",
  },
  appleWebApp: { capable: true, title: "Проход" },
  formatDetection: { telephone: false },
};
export const viewport: Viewport = {
  themeColor: "#172923",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ru">
      <body>
        {children}
        <PwaRegister />
      </body>
    </html>
  );
}
