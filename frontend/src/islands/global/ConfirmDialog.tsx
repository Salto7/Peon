import { useEffect, useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { buttonVariants } from "@/components/ui/button";
import { CONFIRM_EVENT, type ConfirmRequest } from "@/lib/behaviors";

/** Renders confirmation requests raised by ``data-confirm`` forms/buttons. */
export default function ConfirmDialog() {
  const [req, setReq] = useState<ConfirmRequest | null>(null);

  useEffect(() => {
    const onConfirm = (e: Event) => setReq((e as CustomEvent<ConfirmRequest>).detail);
    window.addEventListener(CONFIRM_EVENT, onConfirm);
    return () => window.removeEventListener(CONFIRM_EVENT, onConfirm);
  }, []);

  return (
    <AlertDialog open={!!req} onOpenChange={(o) => !o && setReq(null)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{req?.title ?? "Are you sure?"}</AlertDialogTitle>
          <AlertDialogDescription>{req?.message}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className={req?.destructive ? buttonVariants({ variant: "destructive" }) : undefined}
            onClick={() => {
              req?.onConfirm();
              setReq(null);
            }}
          >
            {req?.confirmLabel ?? "Continue"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
