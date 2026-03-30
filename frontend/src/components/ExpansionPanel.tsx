import { useState } from 'react';
import { ChevronDown } from 'lucide-react';

export function ExpansionPanel({
  title,
  icon: Icon,
  defaultOpen = true,
  id,
  className = '',
  children,
}: {
  title: string;
  icon?: React.ComponentType<{ className?: string }>;
  defaultOpen?: boolean;
  id?: string;
  className?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`hw-surface overflow-hidden ${className}`} id={id}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="w-full flex items-center justify-between px-5 py-3 border-b border-border hover:bg-surface-alt/30 transition-colors"
      >
        <div className="flex items-center gap-3">
          {Icon && <Icon className="w-4 h-4 text-[#4A4B50]" />}
          <span className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">{title}</span>
        </div>
        <ChevronDown className={`w-4 h-4 text-[#4A4B50] transition-transform duration-200 ${open ? '' : '-rotate-90'}`} />
      </button>
      {open && <div className="p-5">{children}</div>}
    </section>
  );
}

export function ExpansionPanelAlt({
  title,
  defaultOpen = true,
  id,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  id?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="hw-surface-alt overflow-hidden" id={id}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-2.5 border-b border-border hover:bg-surface-alt/30 transition-colors"
      >
        <span className="text-[10px] font-bold text-[#8E9299] uppercase tracking-[0.2em]">{title}</span>
        <ChevronDown className={`w-3.5 h-3.5 text-[#4A4B50] transition-transform duration-200 ${open ? '' : '-rotate-90'}`} />
      </button>
      {open && <div className="p-4">{children}</div>}
    </section>
  );
}
