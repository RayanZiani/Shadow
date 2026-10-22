import { useState } from "react";

/**
 * Pavé numérique du terminal (surface d'attaque physique).
 *
 * Simule le clavier du terminal physique de caisse (TPE).
 * Un code valide exécute une transaction bLSAG réelle.
 * Des codes erronés répétés génèrent des échecs qui alimentent le modèle ML.
 */
export default function PinPad({ apiBase, onTransaction }) {
  const [digits, setDigits] = useState("");
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  const press = (digit) => {
    if (digits.length >= 4) return;
    setDigits(digits + digit);
    setMessage(null);
  };

  const autoFillDemo = () => {
    // PIN de démonstration par défaut généré dans .env
    setDigits("5771");
    setMessage(null);
  };

  const submit = async () => {
    if (digits.length !== 4 || busy) return;
    setBusy(true);
    try {
      const response = await fetch(`${apiBase}/pin/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin: digits, amount: 1 }),
      });
      if (!response.ok) {
        setMessage({ ok: false, text: "CODE REFUSÉ — Signal envoyé au ML" });
        setDigits("");
        return;
      }
      // PIN accepté : on paie pour de vrai (transaction de démonstration).
      const decoys = await fetch(`${apiBase}/decoys?count=10`).then((r) => r.json());
      const pool = await fetch(`${apiBase}/outputs?unspent=true`).then((r) => r.json());
      const real = pool.outputs?.[Math.floor(Math.random() * (pool.outputs?.length || 1))];
      const tx = await fetch(`${apiBase}/tx/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ real_output_id: real?.id, decoy_ids: decoys.decoys }),
      });
      const body = await tx.json();
      if (tx.ok) {
        setMessage({ ok: true, text: `PAIEMENT CONFIRMÉ · montant ${body.amount}` });
        onTransaction?.(body);
      } else {
        setMessage({ ok: false, text: `ÉCHEC : ${body.detail ?? tx.status}` });
      }
    } catch {
      setMessage({ ok: false, text: "API INJOIGNABLE" });
    } finally {
      setBusy(false);
      setDigits("");
    }
  };

  return (
    <div className="flex flex-col items-center">
      {/* 4 cercles de saisie du PIN */}
      <div className="flex items-center justify-center gap-3 my-2">
        {[0, 1, 2, 3].map((index) => (
          <span
            key={index}
            className={`w-5 h-5 rounded-full border-2 transition-all ${
              digits.length > index
                ? "bg-ink border-ink scale-110 shadow-sm"
                : "border-ink/40 bg-page/50"
            }`}
          />
        ))}
      </div>

      {/* Grille du pavé */}
      <div className="grid grid-cols-3 gap-2 w-full max-w-[240px] my-1">
        {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((digit) => (
          <button
            key={digit}
            onClick={() => press(String(digit))}
            className="bg-white/90 hover:bg-white text-ink text-base font-bold rounded-lg py-2 transition shadow-sm border border-rule/30 active:scale-95"
          >
            {digit}
          </button>
        ))}
        <button
          onClick={() => {
            setDigits("");
            setMessage(null);
          }}
          title="Effacer"
          className="bg-white/60 hover:bg-white text-ink/70 font-bold rounded-lg py-2 text-xs transition border border-rule/30 active:scale-95"
        >
          C
        </button>
        <button
          onClick={() => press("0")}
          className="bg-white/90 hover:bg-white text-ink text-base font-bold rounded-lg py-2 transition shadow-sm border border-rule/30 active:scale-95"
        >
          0
        </button>
        <button
          onClick={submit}
          disabled={digits.length !== 4 || busy}
          className="bg-ink hover:bg-ink/90 text-white font-bold rounded-lg py-2 text-xs transition shadow-sm disabled:opacity-40 active:scale-95"
        >
          {busy ? "…" : "OK"}
        </button>
      </div>

      {/* Message de résultat */}
      {message && (
        <div
          className={`mt-2 px-3 py-1 rounded-md text-xs font-bold text-center w-full max-w-[240px] ${
            message.ok ? "bg-emerald/20 text-emerald border border-emerald/40" : "bg-crimson/20 text-crimson border border-crimson/40"
          }`}
        >
          {message.text}
        </div>
      )}

      {/* Aide et raccourci démo */}
      <div className="mt-3 flex items-center justify-between w-full max-w-[240px] text-[11px] text-ink/65">
        <span>PIN démo : <strong className="text-ink font-mono">5771</strong></span>
        <button
          type="button"
          onClick={autoFillDemo}
          className="text-ink font-semibold underline hover:text-ink/90"
        >
          Insérer 5771
        </button>
      </div>
    </div>
  );
}
