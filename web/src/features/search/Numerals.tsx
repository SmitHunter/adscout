/** Sets the digit runs in `text` in the numeral face, leaving the words in Archivo. */
export function Numerals({ text }: { text: string }) {
  // Standalone numbers only: "M4", "M42" and "v0.1.0" stay words. A run starts
  // only where no letter, digit or number punctuation precedes it.
  const parts = text.split(/((?<![A-Za-z\d.,:])\d[\d,.:]*)/);
  return (
    <>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <span key={index} className="num">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </>
  );
}

/**
 * A grouped integer at display size. Martian Mono gives the comma a full digit
 * cell, which reads as "1 , 284" when large; the separators are pulled in so the
 * groups sit together. Screen readers get the plain number.
 */
export function DisplayInt({ text, className }: { text: string; className?: string }) {
  const parts = text.split(/([,.])/);
  return (
    <span className={className}>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <span key={index} data-sep="" className="mx-[-0.22em]">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </span>
  );
}
