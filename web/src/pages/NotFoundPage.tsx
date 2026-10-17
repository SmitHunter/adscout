import { Link } from "react-router";

import { buttonVariants } from "@/components/ui/button";

export function NotFoundPage() {
  return (
    <section className="max-w-xl space-y-4 pt-16">
      <h1 className="text-24 font-semibold text-text">This page doesn't exist.</h1>
      <Link to="/" className={buttonVariants({ variant: "outline" })}>
        Start a new search
      </Link>
    </section>
  );
}
