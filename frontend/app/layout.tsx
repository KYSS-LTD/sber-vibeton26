import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Проход · контроль объекта",
  description: "Смены, проходная и присутствие на строительной площадке",
  manifest: "/manifest.json",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
