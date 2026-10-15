import { ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';

export default function DetailsSummary({children}:{children:ReactNode}) {
 return <summary className="details-summary"><ChevronRight size={16} aria-hidden="true"/><span>{children}</span></summary>;
}
