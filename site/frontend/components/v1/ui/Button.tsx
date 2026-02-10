type ButtonProps = {
  children: React.ReactNode;
  variant?: 'primary' | 'secondary';
  className?: string;
  type?: 'button' | 'submit' | 'reset';
  onClick?: () => void;
};

const cn = (...classes: Array<string | undefined>) => classes.filter(Boolean).join(' ');

export default function Button({
  children,
  variant = 'primary',
  className,
  type = 'button',
  onClick,
}: ButtonProps) {
  const base =
    'inline-flex items-center justify-center rounded-full text-sm font-semibold transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] focus-visible:outline-none';
  const styles =
    variant === 'primary'
      ? 'bg-white text-black hover:scale-[1.03] hover:shadow-[0_0_24px_rgba(255,255,255,0.2)]'
      : 'border border-white/15 bg-white/5 text-white hover:bg-white/10 hover:scale-[1.03]';

  return (
    <button type={type} onClick={onClick} className={cn(base, styles, className)}>
      {children}
    </button>
  );
}
