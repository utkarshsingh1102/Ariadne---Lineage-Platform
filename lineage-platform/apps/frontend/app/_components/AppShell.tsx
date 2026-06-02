"use client";

import {
  Header,
  HeaderContainer,
  HeaderGlobalAction,
  HeaderGlobalBar,
  HeaderMenuButton,
  HeaderName,
  HeaderNavigation,
  HeaderMenuItem,
  SideNav,
  SideNavItems,
  SideNavLink,
  SkipToContent,
  Theme,
} from "@carbon/react";
import {
  Catalog,
  ChartNetwork,
  CloudUpload,
  Dashboard,
  Folder,
  Notification,
  TimePlot,
  UserAvatar,
} from "@carbon/icons-react";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode } from "react";

interface NavEntry {
  label: string;
  href: string;
  icon: any;
}

const NAV: NavEntry[] = [
  { label: "Dashboard", href: "/", icon: Dashboard },
  { label: "Files", href: "/files", icon: Folder },
  { label: "Graph explorer", href: "/explorer", icon: ChartNetwork },
  { label: "Lineage tracer", href: "/lineage", icon: Catalog },
  { label: "TWS operations", href: "/tws", icon: TimePlot },
  { label: "Parse a source", href: "/parse", icon: CloudUpload },
];

export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() ?? "/";

  return (
    <Theme theme="white">
      <HeaderContainer
        render={({ isSideNavExpanded, onClickSideNavExpand }: any) => (
          <>
            <Header aria-label="Lineage Platform">
              <SkipToContent />
              <HeaderMenuButton
                aria-label={isSideNavExpanded ? "Close menu" : "Open menu"}
                onClick={onClickSideNavExpand}
                isActive={isSideNavExpanded}
              />
              <HeaderName href="/" prefix="Lineage">
                Platform
              </HeaderName>
              <HeaderNavigation aria-label="Lineage Platform">
                <HeaderMenuItem href="/files">Files</HeaderMenuItem>
                <HeaderMenuItem href="/explorer">Explorer</HeaderMenuItem>
                <HeaderMenuItem href="/lineage">Lineage</HeaderMenuItem>
                <HeaderMenuItem href="/tws">TWS</HeaderMenuItem>
                <HeaderMenuItem href="/parse">Parse</HeaderMenuItem>
              </HeaderNavigation>
              <HeaderGlobalBar>
                <HeaderGlobalAction aria-label="Notifications" tooltipAlignment="end">
                  <Notification size={20} />
                </HeaderGlobalAction>
                <HeaderGlobalAction aria-label="User profile" tooltipAlignment="end">
                  <UserAvatar size={20} />
                </HeaderGlobalAction>
              </HeaderGlobalBar>
              <SideNav
                aria-label="Side navigation"
                expanded={isSideNavExpanded}
                isPersistent={false}
              >
                <SideNavItems>
                  {NAV.map((item) => {
                    const Icon = item.icon;
                    const isActive =
                      item.href === "/"
                        ? pathname === "/"
                        : pathname.startsWith(item.href);
                    return (
                      <SideNavLink
                        key={item.href}
                        renderIcon={Icon}
                        isActive={isActive}
                        onClick={(e: any) => {
                          e.preventDefault();
                          router.push(item.href);
                        }}
                        href={item.href}
                      >
                        {item.label}
                      </SideNavLink>
                    );
                  })}
                </SideNavItems>
              </SideNav>
            </Header>
            <main
              id="main-content"
              className={`app-content ${
                isSideNavExpanded ? "app-content--with-sidenav" : ""
              }`}
            >
              {children}
            </main>
          </>
        )}
      />
    </Theme>
  );
}
