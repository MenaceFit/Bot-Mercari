'use client';

/**
 * L'historique a fusionné avec le flux. Cette page ne sert qu'à ne pas
 * casser un favori : un 404 pour quelqu'un qui avait rangé le lien serait
 * une régression gratuite.
 */

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function Page() {
  const router = useRouter();
  useEffect(() => { router.replace('/feed'); }, [router]);
  return (
    <p className="text-2xs text-faint p-4">
      L’historique fait maintenant partie du flux — redirection…
    </p>
  );
}
