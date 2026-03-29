# Exemple ACEBench

Ceci est un exemple d'évaluation orientée agents dans AgentScope.

Nous prenons [ACEBench](https://github.com/ACEBench/ACEBench) comme benchmark d'exemple, et exécutons
un agent ReAct avec un évaluateur basé sur [Ray](https://github.com/ray-project/ray), qui prend en charge
l'évaluation **distribuée** et **parallèle**.

Pour exécuter l'exemple, vous devez d'abord installer AgentScope, puis lancer l'évaluation avec la commande suivante :

```bash
python main.py --data_dir {data_dir} --result_dir {result_dir}
```

## Lectures complémentaires

- [ACEBench](https://github.com/ACEBench/ACEBench)
- [Ray](https://github.com/ray-project/ray)
