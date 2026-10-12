# Três Poderes

Quanto custa cada pessoa dos três poderes do Brasil, com dados abertos oficiais.

**Em construção.** Ainda não existe site. Hoje o projeto carrega e confere uma única fonte: a cota
parlamentar da Câmara dos Deputados.

## O que já funciona

- Baixa os arquivos anuais da cota parlamentar publicados pela Câmara (2008 a 2026).
- Carrega cada recibo em um banco SQLite, com os valores em centavos inteiros.
- Guarda, para cada recibo, de qual arquivo e de qual linha ele veio.
- Confere o banco contra os arquivos: total do ano, total de cada deputado e conteúdo.

## O que ainda falta saber antes de confiar nos totais

Os arquivos anuais da Câmara deixaram de trazer as passagens aéreas a partir de agosto de 2025. Elas
continuam na API da Câmara. Enquanto as duas fontes não forem cruzadas aqui, **os totais de 2025 e de
2026 estão incompletos**.

## Como rodar

Testado com Python 3.14. Não há nada para instalar.

```
python tools/download_camara_cota.py     baixa os arquivos (cerca de 140 MB)
python -m tres_poderes load-cota         carrega no banco (cerca de 1 minuto)
python -m tres_poderes verify-cota       confere o banco contra os arquivos
check.cmd                                roda os testes
```

## Regras do projeto

- Mostrar os números e comparar, nunca dar nota.
- A mesma medida para todas as pessoas, partidos e poderes.
- Dinheiro sempre em centavos inteiros. Um valor que precisaria ser arredondado é recusado.
- Todo número pode ser rastreado até o arquivo oficial e a linha.

## Fonte dos dados

[Dados Abertos da Câmara dos Deputados](https://dadosabertos.camara.leg.br/).
