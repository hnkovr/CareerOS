"use client";

import { Briefcase, FileText, LayoutDashboard, Library, UserCircle } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const items = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/vault", label: "Vault", icon: Library },
  { href: "/opportunities", label: "Opportunities", icon: Briefcase },
  { href: "/cv", label: "CV", icon: FileText },
  { href: "/profiles", label: "Profiles", icon: UserCircle },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="flex items-center gap-1">
      {items.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors ${
              active ? "bg-accent/15 text-accent" : "text-ink-dim hover:bg-panel-2 hover:text-ink"
            }`}
          >
            <Icon className="h-4 w-4" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
