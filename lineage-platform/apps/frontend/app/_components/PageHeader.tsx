import { Breadcrumb, BreadcrumbItem } from "@carbon/react";
import { ReactNode } from "react";

interface Props {
  title: string;
  subtitle?: string;
  breadcrumbs?: { label: string; href?: string; current?: boolean }[];
  actions?: ReactNode;
}

export function PageHeader({ title, subtitle, breadcrumbs, actions }: Props) {
  return (
    <header className="page-header">
      {breadcrumbs && (
        <Breadcrumb>
          {breadcrumbs.map((b, i) => (
            <BreadcrumbItem
              key={i}
              href={b.href ?? "#"}
              isCurrentPage={b.current}
            >
              {b.label}
            </BreadcrumbItem>
          ))}
        </Breadcrumb>
      )}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
          gap: "1rem",
          flexWrap: "wrap",
        }}
      >
        <div>
          <h1 className="page-header__title">{title}</h1>
          {subtitle && <p className="page-header__subtitle">{subtitle}</p>}
        </div>
        {actions}
      </div>
    </header>
  );
}
